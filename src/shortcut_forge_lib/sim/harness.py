#!/usr/bin/env python3
"""Drive the iOS Simulator well enough to install and run a Shortcut.

Everything here was established by experiment against a booted iOS 27 sim; the
non-obvious findings are recorded in docs/simulator-harness.md. The three that shape this file:

  * A shortcut is installed by opening it as a *host* file URL. Simulator
    processes see the Mac's filesystem, so `file:///Users/...` resolves.
    `shortcuts://import-shortcut` is iCloud-only and will not take a local file.
  * A synthesized click needs a MouseMoved event first and ClickState set, or
    the cursor moves and nothing is pressed.
  * Consent prompts ("allow this shortcut to connect to localhost") block the
    run. The affirmative button is always the bottom-most iOS-blue one, which
    is enough to dismiss every prompt shape without reading any text.

Xcode 27 deleted Simulator.app and replaced it with Device Hub, which changed
every one of those clicks' addresses but none of their logic. See the _Host
classes below for what differs and docs/simulator-harness.md for how it was established.
"""

from __future__ import annotations

import json
import os
import plistlib
import sqlite3
import subprocess
import tempfile
import time
import urllib.parse
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import numpy as np
import Quartz as _Quartz
from PIL import Image

from shortcut_forge_lib.sim import ax, idb

if TYPE_CHECKING:
    from collections.abc import Callable

# pyobjc populates the Quartz namespace lazily and ships no stubs, so every
# attribute on it is unresolved to a type checker. One cast here instead of an
# ignore on each of the twenty call sites.
Quartz = cast("Any", _Quartz)

# iOS system blue, as rendered on the alert buttons.
BLUE_MIN_B = 200
BLUE_MAX_R = 110
BLUE_MIN_SPREAD = 110  # blue channel must lead red by this much
BUTTON_MIN_W = 360  # device px; a half-width alert button
# US virtual keycodes. The Simulator forwards raw HID codes to the guest, so
# these — not unicode strings — are what actually reach a text field. Enough
# for a 6-digit code and the word "resend".
KEYCODES = {
    "0": 29,
    "1": 18,
    "2": 19,
    "3": 20,
    "4": 21,
    "5": 23,
    "6": 22,
    "7": 26,
    "8": 28,
    "9": 25,
    "a": 0,
    "b": 11,
    "c": 8,
    "d": 2,
    "e": 14,
    "f": 3,
    "g": 5,
    "h": 4,
    "i": 34,
    "j": 38,
    "k": 40,
    "l": 37,
    "m": 46,
    "n": 45,
    "o": 31,
    "p": 35,
    "q": 12,
    "r": 15,
    "s": 1,
    "t": 17,
    "u": 32,
    "v": 9,
    "w": 13,
    "x": 7,
    "y": 16,
    "z": 6,
    " ": 49,
}

BUTTON_H_RANGE = (70, 220)  # excludes the tall shortcut tile on
# the import sheet, which is also blue


DARK_SUM = 150  # r+g+b below this is bezel, or a dark background

IDB = "idb"
"""Found on PATH, like the validator and the signer. `brew trust facebook/fb && brew install facebook/fb/idb`."""

#: The labels only the Shortcuts *runner* draws, in `com.apple.ShortcutsUI` —
#: a different process from the frontmost app, which no tree query can reach.
#: They are looked up by hit test at a measured position and never in the tree,
#: because the frontmost tree has buttons by the same names: the screen under a
#: dialog carries its own Done and Cancel, and the software keyboard's return
#: key carries AXLabel "Done" as a Key.
DONT_ALLOW = "Don\u2019t Allow"
"""The device writes this label with a typographic apostrophe (U+2019), not the straight one.

Written as an escape rather than as the character, because the two are
indistinguishable in most editors and an exact-label comparison against the
wrong one silently finds nothing. Measured 2026-09-19: it was the one label the
first fixture capture never saw, because the capture asked for the straight
form. Apostrophes are not normalized anywhere in the finder — a silent
transformation would hide the next label that does not match for some other
reason.
"""

DIALOG_LABELS = frozenset({"Done", "Cancel", "Allow", "Always Allow", "Allow Once", DONT_ALLOW})

#: Where those dialogs put their buttons, as fractions of the screen, measured
#: by `tests/capture_idb_fixtures.py` and recorded in `tests/fixtures/idb/`.
#: Order matters: *Always Allow* is tried before *Allow*, because a consent
#: answered with the other choice asks again on the next run. A dialog no seed
#: finds raises with a screenshot rather than being swept for — a sweep costs
#: 25 s or more, and the consumer polls for the *absence* of a dialog every
#: second. Measuring the new shape once and adding a row here is the fix.
SEEDS: tuple[tuple[str, float, float], ...] = (
    ("Always Allow", 0.500, 0.665),  # (201, 581) on 402x874
    ("Allow", 0.729, 0.200),  # (293, 174)
    ("Allow Once", 0.500, 0.589),  # (201, 514)
    (DONT_ALLOW, 0.271, 0.200),  # (108, 174)
    ("Done", 0.729, 0.347),  # (293, 303)
    ("Cancel", 0.271, 0.347),  # (108, 303)
)

#: Where the runner's Ask for Input dialog puts its field.
ASK_FIELD: tuple[float, float] = (0.500, 0.233)  # (201, 203)

#: What a text field is called, in either backend.
FIELD_TYPES = ("TextField", "TextArea")


class SimulatorError(RuntimeError):
    pass


def _run(*args: str, check: bool = True, **kw: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=check, **kw)


def _osa(script: str) -> str:
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if r.returncode:
        raise SimulatorError(r.stderr.strip())
    return r.stdout.strip()


def _mouse_click(x: float, y: float, settle: float = 0.7) -> None:
    """A click the guest actually feels.

    A synthesized click needs a MouseMoved event first and ClickState set, or
    the cursor moves and nothing is pressed.
    """
    pt = Quartz.CGPointMake(x, y)

    def post(kind: int, click_state: int | None = None) -> None:
        ev = Quartz.CGEventCreateMouseEvent(
            None,
            kind,
            pt,
            Quartz.kCGMouseButtonLeft,
        )
        # Pin the modifiers off. A posted event otherwise picks up whatever the
        # system currently believes is held, and a stray Command turns a click
        # into a Command-click — which in Device Hub's sidebar adds to the
        # selection instead of replacing it, silently gathering up devices.
        Quartz.CGEventSetFlags(ev, 0)
        if click_state:
            Quartz.CGEventSetIntegerValueField(
                ev,
                Quartz.kCGMouseEventClickState,
                click_state,
            )
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)

    post(Quartz.kCGEventMouseMoved)
    time.sleep(0.2)
    post(Quartz.kCGEventLeftMouseDown, 1)
    time.sleep(0.1)
    post(Quartz.kCGEventLeftMouseUp, 1)
    time.sleep(settle)


def _type_mac(text: str) -> None:
    """Type into a Mac control — Device Hub's sidebar search, not the guest.

    Unicode strings work here. They do not work on the device, which is why
    Simulator.type_text spells everything out in virtual keycodes instead.
    """
    for ch in text:
        for down in (True, False):
            ev = Quartz.CGEventCreateKeyboardEvent(None, 0, down)
            Quartz.CGEventSetFlags(ev, 0)
            Quartz.CGEventKeyboardSetUnicodeString(ev, len(ch), ch)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
            time.sleep(0.02)
        time.sleep(0.04)


def _press_escape() -> None:
    """Clear a focused search field.

    Deliberately not Command-A then Delete. Command-A is Select All, and if the
    click that was meant to focus the field missed, it selects every device in
    the sidebar instead; Delete on a device list is worse still. Escape does
    nothing harmful wherever it lands.
    """
    for down in (True, False):
        ev = Quartz.CGEventCreateKeyboardEvent(None, 53, down)
        Quartz.CGEventSetFlags(ev, 0)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        time.sleep(0.03)
    time.sleep(0.15)


def _title_is(title: str, name: str, version: str = "") -> bool:
    """Does this window title belong to exactly this device?

    A prefix test is not enough on its own: "bw-ios27rc" is a prefix of
    "bw-ios27rc-b", and matching loosely points the whole harness at a
    different device while every check downstream still passes — taps land,
    screenshots come back, and the run reports on a device it never touched.
    The name has to end at a boundary.
    """
    if not title.startswith(name):
        return False
    rest = title[len(name) :]
    return (not rest or rest[0] == " ") and version in rest


def _screen_box(dark: np.ndarray, wall: float = 0.9, gap: int = 2) -> tuple[int, int, int, int] | None:
    """The device screen: the gap between the two walls of dark either side.

    The bezel reads dark in both system appearances. In dark mode so does the
    window background, and in light mode it does not — but that difference
    cannot hurt, because merging the background into the bezel only makes the
    wall thicker and never moves its *inner* edge, which is the edge being
    measured. A column counts as wall when it is dark for almost a whole band
    of rows, which sidebar text and toolbar glyphs never are.

    Dark patches in the wallpaper make extra walls, but they fall between the
    outer two rather than outside them, so the widest interior gap is still the
    screen.
    """
    h, _w = dark.shape
    band = dark[int(h * 0.15) : int(h * 0.85)]
    cols = np.where(band.sum(axis=0) / band.shape[0] > wall)[0]
    span = _widest_gap(_contiguous(cols, gap=gap))
    if span is None:
        return None
    x0, x1 = span
    rows = np.where(dark[:, x0 : x1 + 1].sum(axis=1) / (x1 - x0 + 1) > wall)[0]
    span = _widest_gap(_contiguous(rows, gap=gap))
    if span is None:
        return None
    return (x0, span[0], x1, span[1])


def _widest_gap(runs: list[list[int]]) -> tuple[int, int] | None:
    """The widest space *between* runs, or None if there are fewer than two."""
    gaps = [(runs[i][-1] + 1, runs[i + 1][0] - 1) for i in range(len(runs) - 1)]
    return max(gaps, key=lambda g: g[1] - g[0]) if gaps else None


def _slug(label: str) -> str:
    """A label as a filename fragment, for the screenshot a failure keeps."""
    return "".join(c if c.isalnum() else "-" for c in label.lower()).strip("-") or "button"


def _is_key(e: idb.Element) -> bool:
    """One key of the software keyboard, in either backend's vocabulary.

    The default backend types the keys as ordinary `Button`s — the same type as
    a sheet's own buttons — and marks them with a `KeyboardKey` trait.
    `axbridge` types them `Key` and gives them no traits. Measured 2026-09-19
    on the setup-question page: 33 elements carry the trait in the default
    tree, 37 are typed `Key` in the other, and nothing outside a keyboard
    carries the trait in any capture — the keyboard's own *Close* and its
    predictive-text suggestion do not.

    The trait rather than a list of letters, because the keyboard that matters
    most here is the numeric one: the consumer's code prompt is six digits, and
    a letter list would not see that keyboard at all.
    """
    return e.type == "Key" or "KeyboardKey" in e.traits


# -- the Mac app that draws the device --------------------------------------
#
# Xcode 27 deleted Simulator.app. Device Hub replaces it, showing simulators and
# real devices together in one window with a sidebar, and it differs in every
# way this harness cares about: which process AppleScript has to address, which
# menu connects the hardware keyboard, whether a window for a given device
# exists at all, and how device pixels map to screen points. Simulator.app had
# Point Accurate and a bezel toggle, which made that mapping exact arithmetic on
# the window frame. Device Hub has neither, so the screen has to be found in
# pixels inside the bezel it always draws.
#
# Everything that differs lives in one of the two classes below. Only the Device
# Hub path is exercised now — Xcode 27 leaves no Simulator.app to test against.
# The Simulator.app path is the code that produced the support matrix in
# docs/simulator-harness.md, moved here rather than rewritten.


def _has_window(sim: Simulator) -> bool:
    """Whether the host has a window for this device yet; a missing one raises, so it is caught here."""
    try:
        sim.window_rect()
    except SimulatorError:
        return False
    return True


class _Host:
    """What this harness needs from whichever app is showing the device.

    Windows are addressed by title and menu items by whatever `keyboard_item`
    holds: an AppleScript reference for Simulator.app, which System Events
    can see, and a path of menu titles for Device Hub, which it cannot.
    """

    proc: str | None = None  # the app's process name, for messages
    keyboard_item: Any = None  # menu item that connects the hardware keyboard

    def __init__(self, app: Path) -> None:
        self.app = app

    def launch(self) -> None:
        _run("open", "-a", str(self.app))

    def activate(self) -> None:
        raise NotImplementedError

    def select(self, sim: Simulator) -> None:
        """Make a window for this device exist. Runs before focus_window."""

    def configure(self, sim: Simulator) -> None:
        """Per-window display settings, if this host has any."""

    def mapping(self, sim: Simulator, device_size: tuple[int, int]) -> tuple[float, float, float]:
        """(origin_x, origin_y, screen points per device pixel)."""
        raise NotImplementedError

    # -- windows and menus ---------------------------------------------
    def front_title(self) -> str:
        """The frontmost window's title, or "" when there is none."""
        raise NotImplementedError

    def window_titles(self) -> list[str]:
        raise NotImplementedError

    def window_frame(self, title: str) -> tuple[int, int, int, int]:
        """Raise the window with this title and return (x, y, width, height) in screen points."""
        raise NotImplementedError

    def menu_item(self, item: Any) -> tuple[bool, str | None]:
        """(exists, mark_char) for a menu item; (False, None) when it is absent."""
        raise NotImplementedError

    def menu_click(self, item: Any) -> bool:
        """Click a menu item. False when the frontmost window's menu has no such item."""
        raise NotImplementedError


class _SimulatorApp(_Host):
    """Xcode 26 and earlier, driven through System Events."""

    proc = "Simulator"
    keyboard_item = (
        'menu item "Connect Hardware Keyboard" of menu 1 of menu '
        'item "Keyboard" of menu 1 of menu bar item "I/O" of '
        "menu bar 1"
    )
    WINDOW_MENU = 'menu 1 of menu bar item "Window" of menu bar 1'

    def activate(self) -> None:
        _osa('tell application "Simulator" to activate')

    def _tell(self, script: str) -> str:
        return _osa(f'tell application "System Events" to tell process "{self.proc}" to {script}')

    def front_title(self) -> str:
        try:
            return self._tell("return name of window 1")
        except SimulatorError:
            return ""

    def window_titles(self) -> list[str]:
        raw = self._tell("return name of every window")
        return [t.strip() for t in raw.split(",")] if raw else []

    def window_frame(self, title: str) -> tuple[int, int, int, int]:
        q = title.replace('"', '\\"')
        self._tell(f'perform action "AXRaise" of window "{q}"')
        pos = self._tell(f'return position of window "{q}"')
        size = self._tell(f'return size of window "{q}"')
        x, y = (int(v) for v in pos.split(", "))
        w, h = (int(v) for v in size.split(", "))
        return x, y, w, h

    def menu_item(self, item: Any) -> tuple[bool, str | None]:
        # Menu contents depend on the frontmost window, and a menu item that is
        # not there raises rather than returning empty — so absence has to be
        # caught rather than tested.
        try:
            v = self._tell(f'return value of attribute "AXMenuItemMarkChar" of {item}')
        except SimulatorError:
            return False, None
        return True, (None if v in ("", "missing value") else v)

    def menu_click(self, item: Any) -> bool:
        try:
            self._tell(f"click {item}")
            return True
        except SimulatorError:
            return False

    def select(self, sim: Simulator) -> None:
        # Simulator opens a window per booted device by itself; it can just be
        # windowless for a few seconds after a boot.
        deadline = time.time() + 60
        while time.time() < deadline:
            if _has_window(sim):
                return
            time.sleep(2)
        raise SimulatorError("Simulator never opened a window for this device")

    def configure(self, sim: Simulator) -> None:
        """Point Accurate + no bezels makes device px -> screen points exact.

        Applied tolerantly: another device kind may not offer these, and a
        missing item is worth a note rather than a crash.
        """
        if not sim.menu_click(f'menu item "Point Accurate" of {self.WINDOW_MENU}'):
            warnings.warn(
                "note: no Point Accurate for this window; taps fall back to the window's own scale", stacklevel=2
            )
        time.sleep(0.8)
        bezels = f'menu item "Show Device Bezels" of {self.WINDOW_MENU}'
        exists, marked = sim.menu_item(bezels)
        if exists and marked:
            sim.menu_click(bezels)
            time.sleep(1.0)

    def mapping(self, sim: Simulator, device_size: tuple[int, int]) -> tuple[float, float, float]:
        dw, dh = device_size
        wx, wy, ww, wh = sim.window_rect()
        ppp = ww / dw
        return wx, wy + (wh - dh * ppp), ppp


class _DeviceHub(_Host):
    """Xcode 27 and later, driven through the accessibility API by pid.

    Not through System Events: on macOS 27.0 it lists Device Hub with a unix
    id of 0, no windows and no menu bar, under either of the app's names, and
    the AX API reached by pid sees all three. See `sim/ax.py`.
    """

    proc = "DeviceHub"
    keyboard_item = ("Device", "Keyboard", "Simulate Hardware Keyboard")
    HOME = ("Controls", "Home")
    # Offsets from the window's top-left corner. Nothing in the sidebar reaches
    # the accessibility tree — the split view reports zero children, so there is
    # no row to name and no field to address — which leaves position as the only
    # handle there is. Every click is checked against the window title
    # afterwards, so a miss is loud rather than a tap into the wrong device.
    SEARCH_FIELD = (128, 74)
    FIRST_ROW = (128, 145)
    ROW_PITCH = 46
    ROWS_TO_TRY = 8

    def __init__(self, app: Path) -> None:
        super().__init__(app)
        self._measured: dict[tuple[tuple[int, int, int, int], tuple[int, int]], tuple[float, float, float]] = {}

    # -- the app, through the accessibility tree -----------------------
    def _app(self) -> ax.App:
        app = ax.App.running(self.app)
        if app is None:
            raise SimulatorError("Device Hub is not running")
        return app

    def launch(self) -> None:
        # `open` returns before the app has a window, and a Device Hub that was
        # left running with its window closed gets no new one from a plain
        # `open`; the window is what everything below addresses, so wait for it.
        _run("open", "-a", str(self.app))
        deadline = time.time() + 30
        while time.time() < deadline:
            app = ax.App.running(self.app)
            if app is not None and app.windows():
                return
            time.sleep(1.0)
        raise SimulatorError("Device Hub launched but never showed a window")

    def activate(self) -> None:
        self._app().activate()

    def front_title(self) -> str:
        return self._app().front_title()

    def window_titles(self) -> list[str]:
        return [title for title, _w in self._app().windows()]

    def _window(self, title: str) -> Any:
        # A device can be popped out into a window of its own, which carries
        # the same title as the main window showing it. The main window is the
        # one with the sidebar, and the larger of the two.
        app = self._app()
        matches = [w for t, w in app.windows() if t == title]
        if not matches:
            raise SimulatorError(f"no Device Hub window titled {title!r}")
        return max(matches, key=lambda w: app.frame(w)[2] * app.frame(w)[3])

    def window_frame(self, title: str) -> tuple[int, int, int, int]:
        window = self._window(title)
        ax.raise_window(window)
        return ax.App.frame(window)

    def menu_item(self, item: Any) -> tuple[bool, str | None]:
        element = self._app().menu_item(item)
        if element is None:
            return False, None
        return True, ax.App.mark(element)

    def menu_click(self, item: Any) -> bool:
        element = self._app().menu_item(item)
        return element is not None and ax.press(element)

    # -- picking the device --------------------------------------------
    def select(self, sim: Simulator) -> None:
        """Point Device Hub's one window at this device.

        There is a single window and it shows whichever device the sidebar has
        selected, so "no window for this device" is the ordinary state rather
        than a failure. Filtering by name usually leaves one row, but a name is
        not unique — the same model exists on every installed runtime — so the
        rows are tried in turn and the window title decides.
        """
        name, version = sim.device_label()
        if self._shows(name, version):
            return
        self.activate()
        time.sleep(1.0)
        wx, wy, _, _ = self._frame()
        # Click, Escape, click again. Escape clears whatever the field holds,
        # but on an empty field it moves focus to the "+" button instead —
        # and the name typed next then opens that button's menu and picks an
        # entry by its letters (measured on macOS 27.0: it landed on "Apple
        # TV…" and opened the New Simulator sheet). The second click puts the
        # focus back on the now-empty field either way.
        _mouse_click(wx + self.SEARCH_FIELD[0], wy + self.SEARCH_FIELD[1], settle=0.4)
        _press_escape()
        _mouse_click(wx + self.SEARCH_FIELD[0], wy + self.SEARCH_FIELD[1], settle=0.4)
        _type_mac(name)
        time.sleep(1.2)
        for row in range(self.ROWS_TO_TRY):
            _mouse_click(wx + self.FIRST_ROW[0], wy + self.FIRST_ROW[1] + row * self.ROW_PITCH, settle=0.8)
            if self._shows(name, version):
                return
        raise SimulatorError(
            f"could not select {name} ({version}) in Device Hub's sidebar; the window is showing {self._title()!r}"
        )

    def _title(self) -> str:
        return self.front_title()

    def _shows(self, name: str, version: str) -> bool:
        return _title_is(self._title(), name, version)

    def _frame(self) -> tuple[int, int, int, int]:
        """The main window — the one with the sidebar — which is the largest."""
        app = self._app()
        windows = [w for _t, w in app.windows()]
        if not windows:
            raise SimulatorError("Device Hub has no window")
        return app.frame(max(windows, key=lambda w: app.frame(w)[2] * app.frame(w)[3]))

    def press_home(self, sim: Simulator) -> None:
        if not self.menu_click(self.HOME):
            raise SimulatorError("no Home item in Device Hub's Controls menu")
        time.sleep(1.5)

    # -- where the screen is -------------------------------------------
    def configure(self, sim: Simulator) -> None:
        # The mapping is read off the bezel, and anything dark to the screen's
        # own edge reads as more bezel — so measure on the home screen, once, at
        # the start of a run rather than in the middle of one.
        self.press_home(sim)
        self.mapping(sim, sim.image().size)

    def mapping(self, sim: Simulator, device_size: tuple[int, int]) -> tuple[float, float, float]:
        key = (sim.window_rect(), tuple(device_size))
        if key not in self._measured:
            self._measured[key] = self._measure(sim, key[0], device_size)
        return self._measured[key]

    def _measure(
        self,
        sim: Simulator,
        rect: tuple[int, int, int, int],
        device_size: tuple[int, int],
        tries: int = 5,
    ) -> tuple[float, float, float]:
        """Measure, with patience. A device that booted seconds ago is still
        drawing, and a half-drawn screen has no bezel to find yet. The last
        failure keeps its screenshot and names it, because "could not find the
        bezel" tells you nothing on its own.
        """
        for _attempt in range(tries):
            self.activate()  # a capture of a window behind iTerm2 is
            time.sleep(0.4)  # a capture of iTerm2
            try:
                return self._measure_once(sim, rect, device_size)
            except SimulatorError as e:
                last = e
                time.sleep(2.0)
        keep = (sim.artifacts or Path(tempfile.gettempdir())) / "measure-failed.png"
        _run("cp", str((sim.artifacts or Path(tempfile.gettempdir())) / "_measure.png"), str(keep), check=False)
        raise SimulatorError(f"{last} (window as captured: {keep})")

    def _measure_once(
        self, sim: Simulator, rect: tuple[int, int, int, int], device_size: tuple[int, int]
    ) -> tuple[float, float, float]:
        """Find the device screen in the window, in screen points."""
        wx, wy, ww, wh = rect
        # Clamp the capture to the part of the window that is actually on the
        # display, width included. Clamping only the origin leaves the region
        # running off the far edge by however much was trimmed, and whatever
        # window sits behind there gets measured as bezel.
        ox, oy = max(wx, 0), max(wy, 0)
        cw, ch = ww - (ox - wx), wh - (oy - wy)
        shot = (sim.artifacts or Path(tempfile.gettempdir())) / "_measure.png"
        _run("screencapture", "-x", "-o", f"-R{ox},{oy},{cw},{ch}", str(shot))
        im = Image.open(shot).convert("RGB")
        # `screencapture -R` takes a rect in points and writes *pixels*, so on a
        # Retina display the image is twice the size of the window it captured.
        # Quartz click coordinates are points, so every measurement has to come
        # back through this scale or taps land at half the intended offset.
        scale = im.width / cw
        px = np.asarray(im).astype(int).sum(axis=2)

        box = _screen_box(px < DARK_SUM)
        if box is None:
            raise SimulatorError("no device screen in the window — is it showing a device at all?")
        x0, y0, x1, y1 = box
        sw, sh = (x1 - x0 + 1) / scale, (y1 - y0 + 1) / scale
        dw, dh = device_size
        if not 0.97 <= (sw / sh) / (dw / dh) <= 1.03:
            raise SimulatorError(
                f"measured a {sw:.0f}x{sh:.0f} screen for a {dw}x{dh} device. "
                f"Something dark to the screen's own edges — the dimmed "
                f"backdrop behind a sheet — has probably swallowed it."
            )
        ppp = ((sw / dw) + (sh / dh)) / 2
        # Fit on the centres — the rounded corners cost a pixel at each edge.
        cx = ox + (x0 + x1) / 2 / scale
        cy = oy + (y0 + y1) / 2 / scale
        return cx - dw / 2 * ppp, cy - dh / 2 * ppp, ppp


def _detect_host() -> _Host:
    dev = Path(_run("xcode-select", "--print-path").stdout.strip())
    simulator = dev / "Applications" / "Simulator.app"
    if simulator.exists():
        return _SimulatorApp(simulator)
    device_hub = dev.parent / "Applications" / "DeviceHub.app"
    if device_hub.exists():
        return _DeviceHub(device_hub)
    raise SimulatorError(
        f"neither Simulator.app nor DeviceHub.app under {dev} — is a full "
        f"Xcode selected? xcode-select --print-path says {dev}"
    )


_HOST: _Host | None = None


def host() -> _Host:
    global _HOST
    if _HOST is None:
        _HOST = _detect_host()
    return _HOST


def __getattr__(name: str) -> _Host:
    if name == "HOST":
        return host()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class Simulator:
    def __init__(self, udid: str, artifacts: str | Path | None = None) -> None:
        self.udid = udid
        self.artifacts = Path(artifacts) if artifacts else None
        if self.artifacts:
            self.artifacts.mkdir(parents=True, exist_ok=True)
        self._shot = 0
        self._screen: tuple[int, int] | None = None

    # -- discovery ------------------------------------------------------
    @classmethod
    def find(cls, runtime: str = "iOS 27", model: str = "iPhone", **kw: Any) -> Simulator:
        """Prefer an already-booted matching device, else the first available."""
        out = _run("xcrun", "simctl", "list", "devices", "available").stdout
        section, booted, first = None, None, None
        for line in out.splitlines():
            if line.startswith("--"):
                section = line.strip("- ").strip()
                continue
            if not section or not section.startswith(runtime):
                continue
            if model not in line or "(" not in line:
                continue
            udid = line.split("(")[1].split(")")[0]
            first = first or udid
            if "(Booted)" in line:
                booted = booted or udid
        chosen = booted or first
        if not chosen:
            raise SimulatorError(
                f"no {runtime} {model} simulator found. Install the runtime in Xcode → Settings → Components."
            )
        sim = cls(chosen, **kw)
        if not booted:
            sim.boot()
        return sim

    # -- lifecycle ------------------------------------------------------
    def boot(self) -> None:
        """Boot the device. Never opens Device Hub — quitting it shuts down every simulator."""
        _run("xcrun", "simctl", "boot", self.udid, check=False)
        self.wait_booted()

    def wait_booted(self, timeout: int = 180) -> None:
        """Wait for Booted, then for the system to actually be usable.

        Three waits, each for a different lie. "Booted" is reported well before
        SpringBoard can service an openurl, and the gap is much wider on the
        first boot after an erase. Shortcuts becomes resolvable a while after
        that. And idb's companion — which this poll is what spawns — answers
        its first read with "No translation object returned" for about four
        seconds after it starts, so the device is not drivable until a tree
        query comes back with something in it.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._is_booted():
                break
            time.sleep(1)
        else:
            raise SimulatorError("simulator did not boot in time")

        while time.time() < deadline:
            r = _run("xcrun", "simctl", "listapps", self.udid, check=False)
            if "com.apple.shortcuts" in r.stdout:
                break
            time.sleep(2)
        else:
            raise SimulatorError("Shortcuts never became available on the device")

        while time.time() < deadline:
            if len(self.elements(idb.AX)) > 1 or len(self.elements(idb.AXBRIDGE)) > 1:
                return
            time.sleep(2)
        raise SimulatorError(
            f"idb never returned an accessibility tree for {self.udid}. A companion may be stuck — "
            f"try `idb disconnect {self.udid}`."
        )

    def prepare(self) -> None:
        """Get this device ready to be driven. Never launches Device Hub.

        Quitting Device Hub shuts down every booted simulator, so the harness
        neither opens it nor depends on it. Someone may keep it open alongside;
        idb's touches do not care whether a window is showing the device.
        """
        if os.environ.get("DEVELOPER_DIR") and self._companion_running():
            raise SimulatorError(
                f"DEVELOPER_DIR is set and a companion is already running for {self.udid}. It loaded "
                f"SimulatorKit from whichever Xcode spawned it and nothing records which, so it may be "
                f"driving this device from the wrong one. Call drop_companion() first, or unset DEVELOPER_DIR."
            )
        if self._is_booted():
            self.wait_booted()
        else:
            self.boot()

    def _is_booted(self) -> bool:
        out = _run("xcrun", "simctl", "list", "devices").stdout
        return any(self.udid in line and "(Booted)" in line for line in out.splitlines())

    def _companion_running(self) -> bool:
        # `-f` has no long form in BSD pgrep; the long-flag rule does not apply.
        return _run("pgrep", "-f", f"idb_companion --udid {self.udid}", check=False).returncode == 0

    def erase(self) -> None:
        """Full clean slate. Also drops the trusted root cert, so re-add it."""
        _run("xcrun", "simctl", "shutdown", self.udid, check=False)
        _run("xcrun", "simctl", "erase", self.udid)
        self.boot()

    def add_root_cert(self, pem: str | Path) -> None:
        _run("xcrun", "simctl", "keychain", self.udid, "add-root-cert", str(pem))

    def terminate_shortcuts(self) -> None:
        _run("xcrun", "simctl", "terminate", self.udid, "com.apple.shortcuts", check=False)

    def set_pasteboard(self, text: str, *, attempts: int = 6) -> None:
        """Put `text` on the device's pasteboard, and confirm it landed.

        `simctl pbcopy` reports success and copies nothing under Xcode 27, so
        the text goes onto the Mac's pasteboard and `simctl pbsync` carries it
        across — which overwrites the Mac's pasteboard; saving and restoring
        it is the caller's business. The sync sometimes lags a beat and the
        read-back shows the previous value, so it is repeated until the two
        agree. A sandboxed shell also reports success and copies nothing,
        which is why the read-back decides. Moved from brightwheel-checkin's
        integration suite, where the clipboard sign-in tests depend on it.
        """
        got = None
        for _ in range(attempts):
            _run("pbcopy", input=text)
            _run("xcrun", "simctl", "pbsync", "host", self.udid)
            time.sleep(0.5)
            got = _run("xcrun", "simctl", "pbpaste", self.udid, check=False).stdout
            if got == text:
                return
            time.sleep(1.5)
        raise SimulatorError(f"the device pasteboard reads {got!r} after copying {text!r}; is the shell sandboxed?")

    # -- idb ------------------------------------------------------------
    def _idb(self, *args: str, timeout: float = 60) -> str:
        """Run one idb command and return its stdout, or raise `SimulatorError`.

        Two of idb's failures are not failures:

        * **"No translation object returned"** is an empty hit test — and a
          fresh companion's first read, about four seconds after it spawns,
          prints the same thing. Both mean "nothing to report", so both come
          back as an empty string and parse as nothing on screen.
        * **"Failed to connect to companion"** means the registry in
          `/tmp/idb/state` outlived the process it names. Dropping that one
          registration lets the next command spawn a fresh companion. Never
          `idb kill`, which SIGKILLs every companion on the Mac — other UDIDs'
          and other sessions'.
        """
        r = _run(IDB, *args, check=False, timeout=timeout)
        if r.returncode == 0:
            return r.stdout
        out = (r.stdout or "") + (r.stderr or "")
        if idb.NOTHING_THERE in out:
            return ""
        if "Failed to connect to companion" in out:
            _run(IDB, *idb.disconnect_args(self.udid), check=False, timeout=30)
            r = _run(IDB, *args, check=False, timeout=timeout)
            if r.returncode == 0:
                return r.stdout
            out = (r.stdout or "") + (r.stderr or "")
            if idb.NOTHING_THERE in out:
                return ""
        raise SimulatorError(f"idb {' '.join(args)} failed: {out.strip()[:400]}")

    def drop_companion(self) -> None:
        """Leave no companion running or registered for this device.

        A companion reads `DEVELOPER_DIR` when it spawns and never again, and
        the registry is keyed by UDID alone, so one spawned under a different
        Xcode is silently reused. Dropping it is how an erase, and a switch of
        Xcode, start from a known state.
        """
        # `-f` has no long form in BSD pkill; this is the documented exception
        # to the long-flag rule, not an oversight.
        _run("pkill", "-f", f"idb_companion --udid {self.udid}", check=False)
        _run(IDB, *idb.disconnect_args(self.udid), check=False)

    # -- reading the screen ---------------------------------------------
    def elements(self, backend: str = idb.AX, *, match: str | None = None) -> list[idb.Element]:
        """The frontmost application's elements. Never another process's.

        Every tree query in idb walks the frontmost application and stops, so
        while the Shortcuts *runner* has a dialog up — Ask for Input, a consent,
        the output-permission sheet, all drawn by `com.apple.ShortcutsUI` — this
        returns the screen underneath it, which has buttons of its own. `at()`
        is the only thing that sees those dialogs.
        """
        return idb.parse_elements(self._idb(*idb.describe_all_args(self.udid, backend=backend, match=match)))

    def _tree(self, *, match: str | None = None) -> list[idb.Element]:
        """The frontmost tree, from whichever backend can see this screen.

        The default backend is asked first: it returns the frontmost
        presentation, and it is the only one that reports a text field. An
        `axbridge` call costs about 4.5 s when it fails, so it is a fallback,
        not a second opinion.

        What counts as "the default backend cannot see this screen" depends on
        whether the read was filtered. Unfiltered, one element means the
        Application and nothing under it. Filtered, the Application does not
        carry a button's label, so any hit at all is a real hit — and asking
        `axbridge` anyway would cost a call on the busiest path in the harness
        and risk answering with an element from *behind* a presented sheet,
        which `--match` cannot tell apart from the sheet's own.
        """
        found = self.elements(idb.AX, match=match)
        enough = bool(found) if match is not None else len(found) > 1
        if enough:
            return found
        other = self.elements(idb.AXBRIDGE, match=match)
        return other if len(other) > len(found) else found

    def at(self, x: float, y: float) -> idb.Element | None:
        """Whatever is under a point, in whichever process drew it. None when nothing is."""
        return idb.parse_element(self._idb(*idb.describe_point_args(self.udid, x, y)))

    def screen_size(self) -> tuple[int, int]:
        """The device's size in points, read once per `Simulator`.

        From the Application element rather than from a screenshot: it is
        already in points, and it is still there when a sheet is up.
        """
        if self._screen is None:
            app = self._application()
            self._screen = (int(app.frame.width), int(app.frame.height))
        return self._screen

    def frontmost_pid(self) -> int:
        """The pid of the app drawing the tree. A hit test that returns another pid is a runner dialog."""
        return self._application().pid

    def _application(self) -> idb.Element:
        for backend in (idb.AX, idb.AXBRIDGE):
            for e in self.elements(backend):
                if e.type == "Application":
                    return e
        raise SimulatorError(
            f"idb reported no Application element for {self.udid}. The device may still be booting, or a "
            f"companion may be stuck — try `idb disconnect {self.udid}`."
        )

    def _labels(self) -> list[str]:
        """What the frontmost app is showing, for an error message."""
        return [e.label for e in self._tree() if e.label][:15]

    def _keyboard_up(self, tree: list[idb.Element]) -> bool:
        """Is the software keyboard drawn in `tree`? `_is_key()` is what makes this exact."""
        return any(_is_key(e) for e in tree)

    # -- the flows ------------------------------------------------------
    def clear_prompts(self, allow: tuple[str, ...] = ("Always Allow", "Allow"), *, rounds: int = 8) -> list[str]:
        """Answer every consent the runner has up, and report which ones. None up is an empty list.

        Called after `run_shortcut` and at the end of `install`. A URL-started
        run ends on "Allow … to output 1 text item?", and left pending, the
        *next* run finishes in two seconds with no dialog — from outside,
        exactly what a dropped run URL looks like.

        Never *Allow Once*, which asks again on the next run and makes a
        priming pass worthless. Never *Done*, which would submit an empty
        answer to an Ask dialog. Never *OK*: a run error is dismissed by an
        explicit `press("OK")` so that it is not swallowed here.
        """
        pressed: list[str] = []
        for _ in range(rounds):
            found = self.find_button(*allow)
            if found is None:
                return pressed
            pressed.append(self._tap(found))
            time.sleep(1.0)
        raise SimulatorError(f"consent prompts kept coming back after {rounds} rounds: {pressed}")

    def fill(self, text: str) -> str:
        """Type into a text field the *frontmost app* drew, and read it back. Returns what it holds.

        This is the setup-question sheet, not the runner's Ask dialog —
        `answer_prompt` is that one. The read-back is the check the old harness
        never had: a tap that missed the field typed into nothing and reported
        success.
        """
        field = self._field_in_tree()
        if field is None:
            raise SimulatorError(f"no text field in the frontmost app; it showed {self._labels()}")
        self._idb(*idb.tap_args(self.udid, *field.frame.center()))
        time.sleep(1.0)
        if not text:
            # Nothing typed, so there is nothing to read back — and an empty
            # field does not report itself as empty. `AXValue` carries the
            # placeholder when a field has no content: both the Ask dialog and
            # the setup sheet report "Text", with no separate placeholder
            # attribute to tell them apart. Comparing that against "" would
            # wait out the whole timeout and then raise about a field that is
            # behaving normally.
            return ""
        self._idb(*idb.text_args(self.udid, text))
        got = self._settle_value(self._field_in_tree, text)
        if got != text:
            self.screenshot("fill-readback.png")
            raise SimulatorError(f"the field holds {got!r} after typing {text!r}")
        return got

    def _settle_value(self, read: Callable[[], idb.Element | None], want: str, timeout: float = 8.0) -> str:
        """Poll an element's value until it equals `want`; return whatever it holds when time runs out.

        `idb ui text` returns before the field's `AXValue` has caught up.
        Measured 2026-09-19: a read 1.5 s after typing eight characters came
        back one character short, on a device where the same read had been
        verbatim earlier in the session. A single read after a fixed sleep is
        therefore a race, and since the read-back is the entire reason for
        typing through these methods rather than calling `idb ui text`
        directly, losing it to a race would be worse than not having it.

        An empty `want` returns at once, which is what the deliberately empty
        answer needs.
        """
        deadline = time.time() + timeout
        got = ""
        while True:
            seen = read()
            got = (seen.value if seen else None) or ""
            if got == want or time.time() >= deadline:
                return got
            time.sleep(0.5)

    def confirm(self, *labels: str, rounds: int = 6, timeout: float = 45) -> str:
        """Press one of `labels` on a sheet the frontmost app drew, clearing whatever covers it.

        Used after `fill`, where three things can stand between the answer and
        the sheet's own button: the software keyboard, which covers the
        buttons; a first-run typing tip, whose *Continue* hands focus back to
        the field and raises the keyboard again; and the sheet still animating.

        Nothing is tapped blind. The keyboard is dismissed by its own *Close*,
        identified as the one sitting between the middle of the screen and the
        keyboard's top row of keys, because the import sheet carries a *Close*
        of its own at the top. Tapping an empty area — what the old harness did
        — hits the dimmed backdrop here, which dismisses the sheet and loses
        the answer.

        Clearing the keyboard is not optional on this screen: with it up, the
        default backend's tree drops the sheet's buttons entirely, so there is
        nothing to find until it is gone.

        It presses and returns; it does not check that the sheet went away. On
        iOS 27.0 the press lands on a button that does nothing, and the canary
        has to be able to read *not installed* afterwards.
        """
        deadline = time.time() + timeout
        for _ in range(rounds):
            if time.time() > deadline:
                break
            if self._clear_overlay(self._tree()):
                continue
            for label in labels:
                found = self._find_in_tree(label)
                if found is not None:
                    return self._tap(found)
            time.sleep(1.0)
        self.screenshot(f"no-{_slug(labels[0])}.png")
        raise SimulatorError(f"none of {list(labels)} became reachable on the sheet; it showed {self._labels()}")

    def _clear_overlay(self, tree: list[idb.Element]) -> bool:
        """Dismiss one thing covering a sheet's buttons. True when something was dismissed."""
        tip = next((e for e in tree if e.label == "Continue" and e.is_button), None)
        if tip is not None:
            confirmed = self._confirmed(tip)
            if confirmed is not None:
                self._tap(confirmed)
                return True
        keys = [e for e in tree if _is_key(e)]
        if not keys:
            return False
        # The keyboard's own Close sits just above its top row of keys —
        # measured at y 482 with the keys starting at y 597, on an 874-point
        # screen. Both bounds are needed: a sheet carries its own dismiss
        # control at the top, where tapping it dismisses the sheet rather than
        # the keyboard. The import sheet's sits at y 82, labeled *Cancel* in
        # the capture, and a sheet that labels that control *Close* would be
        # indistinguishable from the keyboard's without the lower bound.
        top_key = min(k.frame.y for k in keys)
        _w, h = self.screen_size()
        close = next(
            (e for e in tree if e.label == "Close" and e.is_button and h * 0.4 < e.frame.y < top_key),
            None,
        )
        if close is None:
            return False
        confirmed = self._confirmed(close)
        if confirmed is None:
            return False
        self._tap(confirmed)
        return True

    def _field_in_tree(self) -> idb.Element | None:
        return next((e for e in self._tree() if e.type in FIELD_TYPES), None)

    def _wait_for_field(self, timeout: float) -> idb.Element | None:
        """Poll the Ask dialog's seed until its field answers. None when no dialog came up.

        The dialog belongs to another process, so this is a hit test, not a
        tree read — and it is the one place a missing dialog is an answer
        rather than a failure, because a caller asks "was there a prompt?".
        """
        w, h = self.screen_size()
        x, y = int(w * ASK_FIELD[0]), int(h * ASK_FIELD[1])
        deadline = time.time() + timeout
        while time.time() < deadline:
            seen = self.at(x, y)
            if seen is not None and seen.type in FIELD_TYPES:
                return seen
            time.sleep(1.0)
        return None

    # -- finding a button -------------------------------------------------
    def find_button(self, *labels: str) -> idb.Element | None:
        """The first of `labels` that is actually on screen, or None. Never a guess.

        Two tiers, because idb's two blind spots are complementary:

        1. A label in `DIALOG_LABELS` is hit-tested at each of its seed
           positions, in `SEEDS` order. A hit counts only when it is a button
           carrying exactly that label, which is what stops *Allow Once* from
           answering a probe for *Allow*.
        2. Everything else is looked up in the frontmost tree by exact label —
           `--match` is a substring search, so the label is compared again —
           and then confirmed by one hit test at its center. The confirmation
           is not ceremony: the tree returns the screen *under* a runner dialog
           as readily as the screen itself, and the keyboard covers a sheet's
           own buttons after typing. Without it the harness taps into whatever
           is actually there and reports success.

        A miss costs one hit test per seed for a dialog label, two tree reads
        for anything else.
        """
        dialog = [label for label in labels if label in DIALOG_LABELS]
        if dialog:
            found = self._probe_seeds(dialog)
            if found is not None:
                return found
        for label in labels:
            if label in DIALOG_LABELS:
                continue
            found = self._find_in_tree(label)
            if found is not None:
                return found
        return None

    def press(self, *labels: str) -> str:
        """Find one of `labels` and tap it. Returns the label pressed.

        Raises rather than reporting a tap that landed nowhere, and keeps a
        screenshot: a runner dialog whose position is not in `SEEDS` looks
        exactly like no dialog at all, and the screenshot is what turns it into
        a new seed.
        """
        found = self.find_button(*labels)
        if found is None:
            self.screenshot(f"no-{_slug(labels[0])}.png")
            raise SimulatorError(
                f"none of {list(labels)} is on screen. The frontmost app showed {self._labels()}. "
                f"If a dialog is up that SEEDS does not know about, the screenshot in artifacts says which."
            )
        return self._tap(found)

    def prompt_up(self) -> bool:
        """Is one of the runner's dialogs up? Seeds only: one pass of hit tests, no tree read."""
        return self._probe_seeds(["Always Allow", "Allow", "Done", "Cancel", DONT_ALLOW]) is not None

    def _probe_seeds(self, labels: list[str]) -> idb.Element | None:
        w, h = self.screen_size()
        for label, fx, fy in SEEDS:
            if label not in labels:
                continue
            seen = self.at(int(w * fx), int(h * fy))
            if seen is not None and seen.label == label and seen.is_button:
                return seen
        return None

    def _find_in_tree(self, label: str) -> idb.Element | None:
        for e in self._tree(match=label):
            if e.label == label and e.is_button:
                confirmed = self._confirmed(e)
                if confirmed is not None:
                    return confirmed
        return None

    def _confirmed(self, e: idb.Element) -> idb.Element | None:
        """`e` itself, once a hit test at its center agrees; None when something else is there.

        It returns the element it was given rather than what the hit test
        found, because the two can differ — a hit test names whatever is
        topmost at the point, which may be an inner element with a frame of
        its own — and what a tap needs is the point that was just proven, not
        a fresh center derived from a different rectangle.
        """
        seen = self.at(*e.frame.center())
        if seen is not None and seen.label == e.label and seen.is_button:
            return e
        return None

    def _tap(self, e: idb.Element, *, settle: float = 1.2, tries: int = 4) -> str:
        """Tap an element a hit test has already named, once it has stopped moving.

        A consent sheet slides up, and a tap that lands mid-slide hits whatever
        is at that spot in that frame — *Allow Once* where *Always Allow* is
        about to be. Two readings of the *same point*, a beat apart, that agree
        on the label and on the frame mean it has settled. The point never
        moves between readings: a sliding sheet is what changes under it, which
        is the thing being waited out. The consumer used to do this for itself,
        comparing two screenshots' blue rectangles; it belongs here.
        """
        label = e.label or ""
        x, y = e.frame.center()
        previous = self.at(x, y)
        for _ in range(tries):
            time.sleep(0.6)
            seen = self.at(x, y)
            if seen is None or seen.label != label:
                break
            if previous is not None and seen.frame == previous.frame:
                self._idb(*idb.tap_args(self.udid, x, y))
                time.sleep(settle)
                return label
            previous = seen
        self.screenshot(f"moved-{_slug(label)}.png")
        raise SimulatorError(f"{label!r} moved or vanished before it could be tapped")

    # -- window geometry ------------------------------------------------
    @staticmethod
    def menu_item(item: Any) -> tuple[bool, str | None]:
        """(exists, mark_char) for a menu item of the host app; (False, None) when it is absent."""
        return host().menu_item(item)

    @staticmethod
    def menu_click(item: Any) -> bool:
        """Click a menu item of the host app. False when this window's menu has no such item."""
        return host().menu_click(item)

    def focus_window(self, timeout: int = 20) -> str:
        """Make this device's window frontmost, and confirm it got there.

        Raising is not the same as arriving. Another booted simulator can stay
        in front, and then every menu below belongs to the wrong device — a
        visionOS window has no "Show Device Bezels" at all, so the harness used
        to die on a missing menu item rather than on anything real.
        """
        name, _version = self.device_label()
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.window_rect()  # matches by title, and AXRaises it
            front = host().front_title()
            if _title_is(front, name):
                return front
            time.sleep(0.5)
        raise SimulatorError(
            f"could not bring {name} to the front; another simulator window is holding focus (frontmost was {front!r})"
        )

    def prepare_window(self) -> None:
        """Get this device on screen and make its pixels tappable.

        Every menu the host offers applies to the frontmost window, so this
        device's window is made to exist, brought to the front and *verified*
        before anything is clicked. What "made to exist" and "tappable" mean
        differ per host — see the _Host classes.
        """
        host().launch()
        host().activate()
        host().select(self)
        self.focus_window()
        time.sleep(0.3)
        host().configure(self)
        self.ensure_hardware_keyboard()

    def device_label(self) -> tuple[str, str]:
        """(name, os version) as the Simulator window titles them."""
        out = _run("xcrun", "simctl", "list", "devices", "--json").stdout
        for runtime, devices in json.loads(out)["devices"].items():
            for d in devices:
                if d["udid"] == self.udid:
                    version = runtime.rsplit(".", 1)[-1].replace("iOS-", "").replace("-", ".")
                    return d["name"], version
        raise SimulatorError(f"device {self.udid} not found")

    def window_rect(self) -> tuple[int, int, int, int]:
        """Locate *this* device's Simulator window.

        More than one simulator can be booted at once, and "window 1" is then
        whichever happens to be frontmost — which silently sends every tap to
        the wrong device, or off-screen entirely. Match the title instead.
        """
        name, version = self.device_label()
        titles = host().window_titles()
        match = next((t for t in titles if _title_is(t, name, version)), None)
        if match is None:
            match = next((t for t in titles if _title_is(t, name)), None)
        if match is None:
            raise SimulatorError(f"no {host().proc} window for {name} ({version}); saw {titles}")
        return host().window_frame(match)

    def _mapping(self, device_size: tuple[int, int]) -> tuple[float, float, float]:
        """(origin_x, origin_y, screen points per device pixel)."""
        return host().mapping(self, device_size)

    # -- screen ---------------------------------------------------------
    def screenshot(self, name: str | None = None) -> Path:
        self._shot += 1
        path = (
            self.artifacts / (name or f"shot-{self._shot:03d}.png")
            if self.artifacts
            else Path(tempfile.gettempdir()) / "_sim_shot.png"
        )
        _run("xcrun", "simctl", "io", self.udid, "screenshot", str(path))
        return path

    def image(self, name: str | None = None) -> Image.Image:
        return Image.open(self.screenshot(name)).convert("RGB")

    # -- input ----------------------------------------------------------
    def tap(self, px: int, py: int, device_size: tuple[int, int] | None = None, settle: float = 0.7) -> None:
        """Tap by device-screenshot pixel coordinates."""
        if device_size is None:
            device_size = Image.open(self.screenshot()).size
        host().activate()
        time.sleep(0.35)
        ox, oy, ppp = self._mapping(device_size)
        _mouse_click(ox + px * ppp, oy + py * ppp, settle=settle)

    def type_text(self, text: str) -> None:
        """Type into the focused field, one virtual keycode at a time.

        CGEventKeyboardSetUnicodeString does nothing here: the Simulator passes
        raw keycodes through, so every character arrives as whatever keycode 0
        is and "123456" lands in the field as "Aaaaaa".
        """
        self.ensure_hardware_keyboard()
        host().activate()
        time.sleep(0.3)
        for ch in text:
            code = KEYCODES.get(ch.lower())
            if code is None:
                raise SimulatorError(f"no keycode mapped for {ch!r}")
            for down in (True, False):
                Quartz.CGEventPost(
                    Quartz.kCGHIDEventTap,
                    Quartz.CGEventCreateKeyboardEvent(None, code, down),
                )
                time.sleep(0.03)
            time.sleep(0.06)
        time.sleep(0.5)

    def answer_prompt(self, text: str) -> bool:
        """Fill an Ask for Input dialog and commit it.

        Two traps here. The field is *not* focused when the dialog appears, so
        typing without tapping it first goes nowhere and the answer stays
        empty. And the Done button is iOS blue, so the generic
        tap-the-affirmative would submit that empty answer — which this
        shortcut reads as "send me another code", five times over.
        """
        img = self.image()
        boxes = self.blue_buttons(img)
        if not boxes:
            return False
        w, h = img.size
        done = max(boxes, key=lambda b: (b[3], b[2]))
        # The text field sits directly above the button row.
        self.tap(int(w * 0.25), int(done[1] - h * 0.12), device_size=img.size)
        time.sleep(0.8)
        self.type_text(text)
        time.sleep(0.5)
        return self.tap_affirmative()

    def cancel_prompt(self) -> bool:
        """Dismiss an Ask for Input dialog with its Cancel button. True if there was one.

        Cancel is not blue, so it cannot be found the way Done is. It sits in
        the same row, mirrored across the sheet's center line — measured on
        iOS 27 at the same height as Done and at the width minus Done's own
        center — so it is reached by reflecting Done's box.
        """
        img = self.image()
        boxes = self.blue_buttons(img)
        if not boxes:
            return False
        w, _h = img.size
        x0, y0, x1, y1 = max(boxes, key=lambda b: (b[3], b[2]))
        self.tap(w - (x0 + x1) // 2, (y0 + y1) // 2, device_size=img.size)
        return True

    def ensure_hardware_keyboard(self) -> None:
        """Connect the hardware keyboard, re-applying it even if already checked.

        Synthesized keystrokes only reach the device through the hardware
        keyboard. An erase resets the device side of this while the Simulator
        menu can still show it checked, and the giveaway is the software
        keyboard appearing — at which point typing silently goes nowhere. So
        cycle the setting rather than trusting the tick.
        """
        item = host().keyboard_item
        exists, marked = self.menu_item(item)
        if not exists:
            # Unlike the display settings this one is not optional: without it
            # synthesized keystrokes reach nothing. Say which window owns the
            # menu, because that is the actual problem.
            raise SimulatorError(
                f"no hardware-keyboard item in {host().proc}'s menus — the "
                f"frontmost window is probably another device; call "
                f"focus_window() first"
            )
        clicks = 2 if marked else 1
        for _ in range(clicks):
            self.menu_click(item)
            time.sleep(0.7)

    # -- finding the affirmative button ---------------------------------
    def blue_buttons(self, img: Image.Image | None = None) -> list[tuple[int, int, int, int]]:
        """Boxes of iOS-blue filled buttons, top-to-bottom then left-to-right.

        Used for both "Add Shortcut" on the import sheet and "Allow" /
        "Always Allow" on consent prompts — in every layout the button we want
        is the bottom-most one, so no text recognition is needed.
        """
        img = img or self.image()
        a = np.asarray(img).astype(int)
        r, _g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
        mask = (b > BLUE_MIN_B) & (r < BLUE_MAX_R) & ((b - r) > BLUE_MIN_SPREAD)

        boxes = []
        rows = np.where(mask.sum(axis=1) > 100)[0]
        for band in _contiguous(rows, gap=8):
            y0, y1 = band[0], band[-1]
            if not (BUTTON_H_RANGE[0] <= y1 - y0 <= BUTTON_H_RANGE[1]):
                continue
            cols = np.where(mask[y0 : y1 + 1].sum(axis=0) > (y1 - y0) * 0.4)[0]
            for run in _contiguous(cols, gap=20):
                x0, x1 = run[0], run[-1]
                if x1 - x0 < BUTTON_MIN_W:
                    continue
                boxes.append((int(x0), int(y0), int(x1), int(y1)))
        return boxes

    def tap_affirmative(self, img: Image.Image | None = None) -> bool:
        """Tap the bottom-most blue button. True if there was one."""
        img = img or self.image()
        boxes = self.blue_buttons(img)
        if not boxes:
            return False
        x0, y0, x1, y1 = max(boxes, key=lambda bx: (bx[3], bx[2]))
        self.tap((x0 + x1) // 2, (y0 + y1) // 2, device_size=img.size)
        return True

    # -- shortcuts ------------------------------------------------------
    def install(
        self,
        path: str | Path,
        expect_name: str | None = None,
        timeout: int = 75,
        *,
        skip_setup: bool = True,
    ) -> bool:
        """Open a `.shortcut` as a host file URL and confirm the import sheet.

        The library name comes from the *filename*, not from `WFWorkflowName`.
        Returns True when it installed, False when it was already there.

        With `skip_setup=False` it stops at the setup-question page and returns
        True as soon as that page's text field is in the tree — before the name
        is in the library, and without clearing consents — leaving the sheet
        for `fill` and `confirm`. That is what the canary needs.

        The labels are tried in the order *Skip Setup*, *Add Shortcut*,
        *Set Up Shortcut*: *Skip Setup* exists only on the question page, and
        reaching that page means the sheet forced the setup path, where *Add
        Shortcut* would commit an unanswered question.
        """
        path = Path(path).resolve()
        name = expect_name or path.stem
        if skip_setup and name in self.library():
            return False
        url = "file://" + urllib.parse.quote(str(path))
        # Right after a boot the URL can be refused for a few seconds.
        for _attempt in range(4):
            r = _run("xcrun", "simctl", "openurl", self.udid, url, check=False)
            if r.returncode == 0:
                break
            time.sleep(3)
        else:
            raise SimulatorError(f"could not open {path.name}: {r.stderr.strip()}")

        deadline = time.time() + timeout
        if not skip_setup:
            while time.time() < deadline:
                if self._field_in_tree() is not None:
                    return True
                found = self.find_button("Set Up Shortcut")
                if found is not None:
                    self._tap(found)
                time.sleep(1.5)
            self.screenshot(f"no-setup-page-{name}.png")
            raise SimulatorError(f"{name}: no setup question page within {timeout}s; saw {self._labels()}")

        # Keep confirming until the shortcut actually lands. One press is not
        # enough: after an erase the sheet can take several seconds to draw,
        # and the import writes through CoreData, so re-reading the library too
        # eagerly can miss a confirm that did land.
        pressed = False
        while time.time() < deadline:
            if name in self.library():
                # Seen once in three imports: after Add Shortcut, Shortcuts
                # opened the new shortcut's Apple Intelligence description view
                # instead of returning to the library. Nothing after an install
                # may assume the library is what is on screen.
                self.clear_prompts()
                return True
            found = self.find_button("Skip Setup", "Add Shortcut", "Set Up Shortcut")
            if found is not None:
                pressed = True
                self._tap(found)
            time.sleep(2.0)
        self.screenshot(f"install-failed-{name}.png")
        raise SimulatorError(f"{name} did not install{'' if pressed else ' (no import sheet button ever appeared)'}")

    def run_shortcut(self, name: str) -> None:
        url = "shortcuts://run-shortcut?name=" + urllib.parse.quote(name)
        _run("xcrun", "simctl", "openurl", self.udid, url)

    # -- reading the device's own state ---------------------------------
    @property
    def db_path(self) -> Path:
        """The device's own Shortcuts database. Read-only from here; never write to it."""
        return (
            Path.home()
            / "Library/Developer/CoreSimulator/Devices"
            / self.udid
            / "data/Library/Shortcuts/Shortcuts.sqlite"
        )

    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        if not self.db_path.exists():
            return []
        con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        try:
            return con.execute(sql, params).fetchall()
        finally:
            con.close()

    def shortcut_actions(self, name: str) -> list[dict[str, Any]] | None:
        """The installed shortcut's actions, as the plist list they came from."""
        rows = self._query(
            "SELECT a.ZDATA FROM ZSHORTCUTACTIONS a JOIN ZSHORTCUT s "
            "ON s.Z_PK = a.ZSHORTCUT WHERE s.ZNAME = ? AND s.ZTOMBSTONED = 0",
            (name,),
        )
        if not rows or rows[0][0] is None:
            return None
        return plistlib.loads(bytes(rows[0][0]))

    def library(self) -> list[str]:
        return [r[0] for r in self._query("SELECT ZNAME FROM ZSHORTCUT WHERE ZTOMBSTONED=0")]

    def stored_content(self) -> dict[str, str | None]:
        """Whatever the shortcuts put in Store Content, keyed by its name.

        Store Content is two hops on disk: ZSTOREDVALUE holds the name and a
        keyed archive naming a file, and the value itself lives in that file
        under Library/Shortcuts/PersistentStorage. Reading only the row gives
        you the key and no value, which is misleading rather than empty.
        """
        out = {}
        for name, blob in self._query("SELECT ZDISPLAYNAME, ZVALUE FROM ZSTOREDVALUE WHERE ZTOMBSTONED=0"):
            out[name] = self._stored_value(blob)
        return out

    def _stored_value(self, blob: bytes | None) -> str | None:
        archive_name = _keyed_lookup(_plist(blob), "archiveName")
        if not archive_name:
            return None
        path = self.db_path.parent / "PersistentStorage" / str(archive_name)
        if not path.exists():
            return None
        # Shortcuts wraps the payload differently depending on how it was
        # produced: a scanned/typed string lands under NS.string, while a
        # value carried out of an action arrives as a WFObjectRepresentation
        # whose "object" is the string.
        return _keyed_lookup(_plist(path.read_bytes()), "NS.string", "object")


def _plist(blob: bytes | None) -> Any:
    """Parse a Shortcuts value blob; some carry a one-byte version prefix."""
    if blob is None:
        return None
    raw = bytes(blob)
    if raw[:1] == b"\x01":
        raw = raw[1:]
    try:
        return plistlib.loads(raw)
    except (plistlib.InvalidFileException, ValueError, TypeError):
        return None


def _keyed_lookup(archive: Any, *keys: str) -> str | None:
    """Find the first of `keys` in an NSKeyedArchiver plist, resolved to text."""
    if not isinstance(archive, dict) or "$objects" not in archive:
        return None
    objects = archive["$objects"]

    def resolve(v: Any) -> Any:
        if isinstance(v, plistlib.UID):
            return objects[v.data]
        return v

    for key in keys:
        for obj in objects:
            if isinstance(obj, dict) and key in obj:
                value = resolve(obj[key])
                if isinstance(value, bytes):
                    return value.decode("utf-8", "replace")
                if isinstance(value, str) and value != "$null":
                    return value
    return None


def _contiguous(indices: np.ndarray, gap: int = 1) -> list[list[int]]:
    """Split a sorted index array into runs separated by more than `gap`."""
    runs, current = [], []
    for i in indices:
        if current and i - current[-1] > gap:
            runs.append(current)
            current = []
        current.append(int(i))
    if current:
        runs.append(current)
    return runs
