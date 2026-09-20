#!/usr/bin/env python3
"""Drive the iOS Simulator well enough to install and run a Shortcut.

Everything here was established by experiment against a booted iOS 27 device;
the non-obvious findings are in docs/simulator-harness.md. The four that shape
this file:

  * A shortcut is installed by opening it as a *host* file URL. Simulator
    processes see the Mac's filesystem, so `file:///Users/...` resolves.
    `shortcuts://import-shortcut` is iCloud-only and will not take a local file.
  * The device is driven through idb, which injects touches into it directly:
    no window, no screen mapping, no Accessibility permission, and no Device
    Hub — quitting Device Hub shuts down every booted simulator, so the harness
    never opens it.
  * Every tree query idb offers walks the frontmost application and stops,
    while Shortcuts runs shortcuts out of process. So the Ask for Input dialog,
    the consent alerts and the output-permission sheet are invisible to a tree
    query and are found by hit test instead. See `find_button`.
  * Consent prompts block a run, and one left pending makes the *next* run
    finish in two seconds with nothing to show — which from outside is exactly
    what a dropped run URL looks like. `clear_prompts` after every run.
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
from typing import TYPE_CHECKING, Any

from PIL import Image

from shortcut_forge_lib.sim import idb

if TYPE_CHECKING:
    from collections.abc import Callable

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
#:
#: A label can appear more than once, at different positions, for two dialogs
#: that share a button's text but never their frame: the runner's "One-time
#: automation setup" sheet (shown once per device, the first time any shortcut
#: is run by URL) carries its own *Done* and *Cancel*, well below the Ask for
#: Input dialog's. Measured 2026-09-20 on a 402x874 screen: `com.apple.ShortcutsUI`
#: draws it too — a different pid from the frontmost app, invisible to every
#: tree query, exactly like the Ask dialog and the consent alerts — with Done at
#: `{{207, 600}, {172, 54}}` and Cancel at `{{23, 600}, {172, 54}}`. `_probe_seeds`
#: checks entries in order and stops at the first hit, so this row costs one
#: extra miss on the ordinary Ask-dialog lookups and never conflates the two:
#: whichever sheet is actually on screen is the only one a hit test can find.
#:
#: The network consent — "Allow ... to connect to localhost?" — is a second
#: unmeasured shape found the same day: four lines of body text push its
#: buttons well below the shorter clipboard consent's, at `{{207, 250.67},
#: {172, 54}}` for Allow and `{{23, 250.67}, {172, 54}}` for Don't Allow, also
#: `com.apple.ShortcutsUI`. *Allow* was already in `clear_prompts`'s default
#: set, so this row alone was the fix; nothing else needed to change.
#:
#: The clipboard-send consent — "Allow ... to send 1 text item to
#: 'localhost'?" — is a third, measured the same day: a value that came off
#: the clipboard earns its own consent the first time it is sent anywhere, and
#: this one's three buttons are each full-width and stacked (*Don't Allow*,
#: *Allow Once*, *Always Allow*, top to bottom), not split left and right like
#: every other dialog here — so all three sit at x fraction 0.500, not 0.729 or
#: 0.271. Frames: `{{23, 483}, {356, 54}}` (Don't Allow), `{{23, 549}, {356,
#: 54}}` (Allow Once), `{{23, 615}, {356, 54}}` (Always Allow), all
#: `com.apple.ShortcutsUI`. *Always Allow* was already in `clear_prompts`'s
#: default set, so this row alone was the fix.
#:
#: That same consent appears at a second position, 207 points higher, when its
#: title reads "send 1 *Clipboard* item" rather than "send 1 text item" —
#: measured 2026-09-20 from twelve consecutive gate failures that every one of
#: them showed at the identical place: `{{23, 275.7}, {356, 54}}` (Don't
#: Allow), `{{23, 341.7}, {356, 54}}` (Allow Once), `{{23, 407.7}, {356, 54}}`
#: (Always Allow). The two blue buttons were measured off those screenshots at
#: 3 px/pt and *Don't Allow* follows from the stack's 66-point pitch, which
#: both shapes share. These dialogs are neither centered nor anchored to an
#: edge: the buttons sit wherever the title and body leave them, so a consent
#: worded differently is another row rather than an offset on this one.
SEEDS: tuple[tuple[str, float, float], ...] = (
    ("Always Allow", 0.500, 0.665),  # (201, 581) on 402x874 — the output-permission sheet
    ("Allow", 0.729, 0.200),  # (293, 174) — the clipboard consent
    ("Allow Once", 0.500, 0.589),  # (201, 514) — the output-permission sheet
    (DONT_ALLOW, 0.271, 0.200),  # (108, 174) — the clipboard consent
    ("Done", 0.729, 0.347),  # (293, 303) — the Ask for Input dialog
    ("Cancel", 0.271, 0.347),  # (108, 303) — the Ask for Input dialog
    ("Allow", 0.729, 0.318),  # (293, 278) — "connect to localhost", measured 2026-09-20
    (DONT_ALLOW, 0.271, 0.318),  # (109, 278) — "connect to localhost", measured 2026-09-20
    ("Done", 0.729, 0.717),  # (293, 627) — the one-time automation setup sheet, measured 2026-09-20
    ("Cancel", 0.271, 0.717),  # (109, 627) — the one-time automation setup sheet, measured 2026-09-20
    (DONT_ALLOW, 0.500, 0.584),  # (201, 510) — "send 1 text item to localhost", measured 2026-09-20
    ("Allow Once", 0.500, 0.659),  # (201, 576) — "send 1 text item to localhost", measured 2026-09-20
    ("Always Allow", 0.500, 0.735),  # (201, 642) — "send 1 text item to localhost", measured 2026-09-20
    (DONT_ALLOW, 0.500, 0.346),  # (201, 302) — "send 1 Clipboard item to localhost", measured 2026-09-20
    ("Allow Once", 0.500, 0.422),  # (201, 368) — "send 1 Clipboard item to localhost", measured 2026-09-20
    ("Always Allow", 0.500, 0.497),  # (201, 434) — "send 1 Clipboard item to localhost", measured 2026-09-20
)

#: Where the runner's Ask for Input dialog puts its field.
ASK_FIELD: tuple[float, float] = (0.500, 0.233)  # (201, 203)

#: What a text field is called, in either backend. The same field carries a
#: different type name in each: the setup sheet's is a `TextArea` in the
#: default tree and a `TextView` in `axbridge`, at the identical frame and
#: value — measured 2026-09-20, x8 y237.7 w386 h75. `_field_in_tree` reads
#: whichever tree `_tree()` returns, so it has to know both names.
FIELD_TYPES = ("TextField", "TextArea", "TextView")

#: How long a freshly spawned companion answers every hit test with idb's
#: not-there sentence. About four seconds, measured while writing
#: `wait_booted`, which waits the same window out before it calls a device
#: drivable. A negative read inside it establishes nothing, so a companion
#: this session has just replaced is left alone for this long before it is
#: asked anything.
COMPANION_WARMUP = 5.0

#: How long a companion this session replaced is taken at its word.
#: Replacing one costs the warm-up above plus a probe pass, and a fresh
#: companion is blind for the first four seconds of its life — dropping one
#: on every negative is how a run gets *worse*, not better, which was
#: measured the hard way (see `_second_look`). This bounds the replacements a
#: poll loop can provoke to one every three quarters of a minute.
COMPANION_RECHECK_EVERY = 45.0


class SimulatorError(RuntimeError):
    pass


def _run(*args: str, check: bool = True, **kw: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=check, **kw)


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


class Simulator:
    def __init__(self, udid: str, artifacts: str | Path | None = None) -> None:
        self.udid = udid
        self.artifacts = Path(artifacts) if artifacts else None
        if self.artifacts:
            self.artifacts.mkdir(parents=True, exist_ok=True)
        self._shot = 0
        self._screen: tuple[int, int] | None = None
        self._renewed_at: float | None = None
        """When this session last replaced the companion. None means it never did."""

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
        """Full clean slate. Also drops the trusted root cert, so re-add it.

        The companion goes too. It is keyed by UDID alone and survives the
        device it was driving, so the run after an erase starts from a known
        state rather than from whatever the old one still believes.
        """
        _run("xcrun", "simctl", "shutdown", self.udid, check=False)
        self.drop_companion()
        _run("xcrun", "simctl", "erase", self.udid)
        self.boot()

    def prepare_window(self) -> None:
        """Deprecated alias of `prepare()`. There is no window any more."""
        warnings.warn(
            "prepare_window() is now prepare(): the harness drives the device through idb and never "
            "opens a window for it",
            DeprecationWarning,
            stacklevel=2,
        )
        self.prepare()

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

        A device that has been up a long time stops accepting syncs at all, and
        `pbsync` joins `pbcopy` in reporting a success it did not have.
        Measured 2026-09-20: `simctl pbsync --verbose host <udid>` printed
        `Resolved item ... to 13 bytes of data.` and `Sync complete.` while
        `simctl pbpaste` answered *There are no items on the device's
        pasteboard*, for every one of six rounds. Restarting the device's
        `com.apple.coredevice.dtpasteboardd` did not fix it; shutting the
        device down and booting it did, after which the first sync still landed
        nothing and the second worked — so the retry above is right, and this
        is not what it catches. The raise tells the two apart by reading the
        Mac's own pasteboard, because saying "is the shell sandboxed?" to
        someone whose shell is fine costs an hour.
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
        host = _run("pbpaste", check=False).stdout
        if host != text:
            raise SimulatorError(
                f"the Mac's own pasteboard reads {host!r} after copying {text!r}, so there was never anything "
                f"to sync across. A sandboxed shell reports success and copies nothing."
            )
        raise SimulatorError(
            f"the device pasteboard reads {got!r} after {attempts} rounds, while the Mac's holds {text!r}: "
            f"this device has stopped accepting syncs. `xcrun simctl pbsync --verbose host {self.udid}` will "
            f"print the byte count it resolved and `Sync complete` regardless, and restarting "
            f"`com.apple.coredevice.dtpasteboardd` on the device does not help. Shut the device down and boot it."
        )

    # -- idb ------------------------------------------------------------
    def _idb(self, *args: str, timeout: float = 60) -> str:
        """Run one idb command and return its stdout, or raise `SimulatorError`.

        Two of idb's failures are not failures:

        * **"No translation object returned"** is an empty hit test — and a
          fresh companion's first read, about four seconds after it spawns,
          prints the same thing. Both mean "nothing to report", so both come
          back as an empty string and parse as nothing on screen. A *third*
          condition prints that same sentence and means the opposite: a
          long-lived companion stops resolving points inside a runner dialog's
          rectangle while taps there still land, so the dialog blocks the read
          that would find it. That one cannot be told apart here, one call at a
          time; `_probe_seeds` and `_second_look` are where it is caught, and
          `idb.NOTHING_THERE` documents all three.
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
        self._renewed_at = time.time()

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
        presentation, and an `axbridge` call costs about 4.5 s when it fails,
        so it is a fallback, not a second opinion.

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

        The empty list is `find_button`'s earned negative, not a bare probe
        pass: a companion that has gone blind to a dialog's rectangle reports
        no consent in exactly the words an answered one does, and this is the
        method whose silence lets a run time out behind a consent nothing ever
        pressed.
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
        """Wait for the Ask dialog's field. None when no dialog came up — once that is earned.

        A companion that has been alive a long time stops resolving hit tests
        inside a runner dialog's rectangle: `describe-point` answers every
        point in the dialog with the same sentence it uses for an empty one,
        while `idb ui tap` at those coordinates still lands. Measured
        2026-09-20 — a companion an hour old saw nothing of an Ask dialog that
        one ninety seconds old resolved completely, the field included.

        So a timeout here is not yet evidence that no dialog came up, and this
        method's whole contract is that a `None` means exactly that. It drops
        the companion and looks once more before saying so. The cost falls
        entirely on the path that was about to report a negative; a dialog that
        is there is found on the first pass and pays nothing.

        The exception is a companion this session replaced moments ago, which
        is the one companion whose silence is already evidence. `find_button`
        replaces companions for the same reason, and without a shared cooldown
        the consumer's poll loop would have the two of them dropping each
        other's fresh companion faster than either could warm up. The second
        pass here polls for `COMPANION_WARMUP` seconds and more, so it waits
        out the blindness of the companion it just made.
        """
        found = self._poll_for_field(timeout)
        if found is not None or not self._companion_is_suspect():
            return found
        self.drop_companion()
        return self._poll_for_field(min(timeout, 20.0))

    def _poll_for_field(self, timeout: float) -> idb.Element | None:
        """One pass of the wait `_wait_for_field` wraps.

        The dialog belongs to another process, so this is a hit test, not a
        tree read — and it is the one place a missing dialog is an answer
        rather than a failure, because a caller asks "was there a prompt?".

        The pid is what tells the dialog's field from one of the frontmost
        app's own, and it is not optional. Measured: the library's search bar
        runs from y 168 to 212 and the Ask seed is (201, 203), so a hit test
        there finds the search bar whenever the dialog is not up — which is
        most of the several seconds after a run starts. The Ask field reports a
        different pid from the Application element; the setup sheet's field,
        which Shortcuts draws itself, reports the same one. Without this check
        `answer_prompt` taps the search bar and types the answer into it.
        """
        w, h = self.screen_size()
        x, y = int(w * ASK_FIELD[0]), int(h * ASK_FIELD[1])
        deadline = time.time() + timeout
        while time.time() < deadline:
            seen = self.at(x, y)
            # `frontmost_pid()` is read here rather than once before the loop,
            # and only when there is a candidate to judge. `run_shortcut` is
            # preceded by `terminate_shortcuts`, so Shortcuts comes back as a
            # *new* process: a pid read before the poll captures whatever was
            # frontmost before the relaunch — SpringBoard, measured at 10418 in
            # one trace — and every element of the new Shortcuts process then
            # differs from it, which accepts the search bar all over again. The
            # short circuit keeps the cost to one extra call per candidate
            # rather than one per second of waiting.
            if seen is not None and seen.type in FIELD_TYPES and seen.pid != self.frontmost_pid():
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

        The `None` is earned before it is returned. A seed pass that resolved
        nothing at any point it probed has not established an absence — that is
        equally what a companion gone blind to a dialog's rectangle answers —
        and both `clear_prompts` and `prompt_up` run in the consumer's
        sub-second poll loop, where such a negative reads as "the run raised no
        prompt" and leaves a consent sitting unanswered until the whole run
        times out. A pass that resolved something is evidence the companion is
        still answering, so that negative stands as it is. `_second_look` is
        what the other kind costs.

        A miss costs one hit test per seed for a dialog label, two tree reads
        for anything else, and — for a seed pass that proved nothing — a fresh
        companion, at most once every `COMPANION_RECHECK_EVERY` seconds.
        """
        dialog = [label for label in labels if label in DIALOG_LABELS]
        others = [label for label in labels if label not in DIALOG_LABELS]
        answered = True
        if dialog:
            found, answered = self._probe_seeds(dialog)
            if found is not None:
                return found
        for label in others:
            found = self._find_in_tree(label)
            if found is not None:
                return found
        if answered or not self._companion_is_suspect():
            return None
        return self._second_look(dialog)

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
        """Is one of the runner's dialogs up? Seeds only — every label here is one `SEEDS` carries.

        Through `find_button` rather than `_probe_seeds` so that a False is the
        earned one. The consumer calls this to decide that a run URL was
        dropped and start the shortcut over; a run that was merely waiting on a
        consent gets restarted, and the consent is still there afterwards.
        """
        return self.find_button("Always Allow", "Allow", "Done", "Cancel", DONT_ALLOW) is not None

    def _probe_seeds(self, labels: list[str]) -> tuple[idb.Element | None, bool]:
        """Hit-test the seeds for `labels`: what was found, and whether any probe resolved anything.

        The second half of that pair is what makes a negative worth something.
        A hit test answers with idb's not-there sentence in two opposite cases
        — nothing is at the point, or the companion has stopped resolving the
        rectangle a dialog is drawn in — and the sentence is identical. What
        tells them apart is the rest of the pass: a companion that is still
        answering names *something* at a seed, the screen under a dialog or a
        dialog's own dimming view, even when no seed carries the label being
        looked for. A pass where every probe came back empty has established
        nothing whatever.
        """
        w, h = self.screen_size()
        answered = False
        for label, fx, fy in SEEDS:
            if label not in labels:
                continue
            seen = self.at(int(w * fx), int(h * fy))
            if seen is None:
                continue
            answered = True
            if seen.label == label and seen.is_button:
                return seen, True
        return None, answered

    def _companion_is_suspect(self) -> bool:
        """Might the companion's silence be blindness rather than an empty screen?

        False only just after this session replaced it. The registry in
        `/tmp/idb/state` is keyed by UDID alone and outlives the process it
        names, so a companion this session merely *found* can be hours old:
        having never replaced it is exactly as suspect as having replaced it
        long ago, which is why `None` counts as suspect.
        """
        return self._renewed_at is None or time.time() - self._renewed_at >= COMPANION_RECHECK_EVERY

    def _second_look(self, labels: list[str], *, attempts: int = 5) -> idb.Element | None:
        """Probe `labels` again on a companion this call replaces, because the last pass proved nothing.

        A companion that has been alive a long time stops resolving hit tests
        inside a runner dialog's rectangle while `idb ui tap` at the very same
        coordinates still lands. Measured 2026-09-20: a companion an hour old
        saw nothing of an Ask dialog that one ninety seconds old resolved
        completely, the field included. Replacing it is the only cure known.

        Replacing it is also the known way to make a run worse. Doing it at the
        top of `run_shortcut` was implemented, unit-tested green, and produced
        the worst gate run of the session — because a fresh companion answers
        nothing for about four seconds and the consents arrive inside that
        window. So this waits the warm-up out before asking anything, and keeps
        asking until a pass resolves something rather than handing a cold
        companion back to a caller that is about to act on its silence. Past
        that, the silence is as established as this harness can make it.
        """
        self.drop_companion()
        time.sleep(COMPANION_WARMUP)
        for _ in range(attempts):
            found, answered = self._probe_seeds(labels)
            if found is not None or answered:
                return found
            time.sleep(1.0)
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

    # -- device info ------------------------------------------------------
    def device_label(self) -> tuple[str, str]:
        """(name, os version), read from `simctl`."""
        out = _run("xcrun", "simctl", "list", "devices", "--json").stdout
        for runtime, devices in json.loads(out)["devices"].items():
            for d in devices:
                if d["udid"] == self.udid:
                    version = runtime.rsplit(".", 1)[-1].replace("iOS-", "").replace("-", ".")
                    return d["name"], version
        raise SimulatorError(f"device {self.udid} not found")

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
    def tap(self, x: float, y: float, settle: float = 1.0) -> None:
        """Tap at a device point.

        Points, not screenshot pixels: idb's frames, its taps and
        `simctl io screenshot` share one coordinate space, three pixels to the
        point on an iPhone 17 Pro. Nothing is mapped and no window is involved.
        Prefer `press()`, which names what it is tapping first.
        """
        self._idb(*idb.tap_args(self.udid, x, y))
        time.sleep(settle)

    def type_text(self, text: str) -> None:
        """Type into whatever has focus.

        One call, and no hardware-keyboard menu to toggle: idb hands the string
        to the device. `Wi-Fi 123` arrives verbatim, capital and hyphen intact.
        """
        self._idb(*idb.text_args(self.udid, text))
        time.sleep(0.5)

    def answer_prompt(self, text: str, *, expect: str | None = None, timeout: float = 45) -> bool:
        """Fill the runner's Ask for Input dialog and commit it. False when no dialog came up.

        The field is not focused when the dialog appears, so it is tapped
        first; typing without that used to go nowhere and leave the answer
        empty, which this shortcut reads as "send me another code" — five
        times over, because the blue-button rule then pressed *Done* on it.

        The answer is read back before *Done* is pressed. `expect` is for the
        case where the field is known to normalize what was typed; passing it
        makes the difference deliberate instead of invisible.
        """
        want = text if expect is None else expect
        field = self._wait_for_field(timeout)
        if field is None:
            return False
        x, y = field.frame.center()
        self._idb(*idb.tap_args(self.udid, x, y))
        time.sleep(1.0)
        if text:
            self._idb(*idb.text_args(self.udid, text))
        # An empty answer has nothing to read back, and an empty field reports
        # its placeholder as `AXValue` — "Text" on this dialog, measured — so
        # comparing against "" would wait out the timeout and then raise about
        # a field that is behaving normally. One consumer test submits an empty
        # answer on purpose. A caller that passes `expect` has stated what it
        # wants to see, so that is always checked.
        if text or expect is not None:
            got = self._settle_value(lambda: self.at(x, y), want)
            if got != want:
                self.screenshot("answer-readback.png")
                raise SimulatorError(f"the Ask field holds {got!r} after typing {text!r}; expected {want!r}")
        # The dialog sits well above where the keyboard draws, so this is
        # usually a no-op — but a first-run typing tip can cover it on a device
        # that has never been typed into.
        self._clear_overlay(self._tree())
        self.press("Done")
        return True

    def cancel_prompt(self, timeout: float = 45) -> bool:
        """Dismiss an Ask for Input dialog with its Cancel button. False when there was none.

        Cancel used to be unreachable: it is not blue, so it was found by
        reflecting Done's box across the sheet's center line. It is a label now.
        """
        if self._wait_for_field(timeout) is None:
            return False
        self.press("Cancel")
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
