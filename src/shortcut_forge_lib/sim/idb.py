"""Argument construction and JSON parsing for the `idb` CLI.

idb (https://github.com/facebook/idb) injects touches into a booted simulator
through a companion process of its own, so nothing here needs a window, a
screen mapping, or Accessibility permission. Install is `brew trust facebook/fb`
and then `brew install facebook/fb/idb`; Homebrew refuses the formula from an
untrusted tap.

Only argv shapes and parsing live here, the way `guest/tart.py` holds tart's,
so both are tested against captured output with no simulator. Three of the
choices below are measurements rather than preferences:

  * **`--api` is always passed.** The two backends return different trees, and
    which one a caller wants depends on what it is looking for. The default
    (`ax`) returns the frontmost *presentation* and little else: on the setup
    question page, six elements — the heading, the text field, *Add Shortcut*
    and *Skip Setup*. `axbridge` returns the whole window hierarchy, the
    library underneath included, at 158 elements for the same screen. Both
    report the sheet's text field, under different names — a `TextArea` in the
    default tree, a `TextView` in `axbridge`, at the identical frame and value
    — which is why `FIELD_TYPES` knows both. Measured on iOS 27.0, 2026-09-19.
    idb's unspecified default is assumed to be `ax` and not relied on.
  * **`--match` is a substring search.** Its help says "elements whose
    --match-key contains this substring", so a caller wanting an exact label
    compares it again after parsing.
  * **A sentence is not an error.** An empty hit test exits 1 and prints
    `NOTHING_THERE`; so does a fresh companion's first read, about four seconds
    after it spawns. Both mean "nothing to report", and the parsers treat any
    output that is not JSON as nothing on screen.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

AX = "ax"
"""The default accessibility backend: sees a presented sheet, drops the navigation bar."""

AXBRIDGE = "axbridge"
"""The fuller backend: sees the navigation bar and the tiles, not a presented sheet."""

NOTHING_THERE = "No translation object returned"
"""The start of what idb prints for an empty hit test, and for a companion's first read."""


@dataclass(frozen=True)
class Frame:
    """An element's rectangle, in device points.

    Points are the one coordinate space in play: `describe-all` frames, the
    arguments to `idb ui tap`, and a `simctl io screenshot` all share it, at
    three pixels to the point on an iPhone 17 Pro. Nothing is mapped.
    """

    x: float
    y: float
    width: float
    height: float

    def center(self) -> tuple[int, int]:
        """The middle of the rectangle, truncated to whole points.

        Truncated rather than rounded, which can place the point up to a point
        above and left of the true middle. Frames are not always integral — a
        toolbar button measured at x 167.667 — but every control this harness
        taps is scores of points across, and `tap_args` truncates again, so the
        difference never leaves the control.
        """
        return int(self.x + self.width / 2), int(self.y + self.height / 2)


@dataclass(frozen=True)
class Element:
    """One accessibility element, as idb reports it."""

    pid: int
    type: str
    label: str | None
    value: str | None
    frame: Frame
    traits: tuple[str, ...]

    @property
    def is_button(self) -> bool:
        """A button by type. The `Button` trait does not track it and is not consulted.

        Measured across every capture: 183 elements are typed `Button` while
        carrying no `Button` trait — the whole of the `axbridge` tree's Play
        buttons among them — and no element anywhere carries the trait without
        also being typed a button. A trait-based check would therefore miss
        most real buttons and catch nothing the type does not.

        `traits` is parsed regardless, because it is what tells the software
        keyboard's keys apart from a sheet's own buttons: on the default
        backend both are typed `Button`, and only a `KeyboardKey` trait
        separates them.
        """
        return self.type == "Button"


def describe_all_args(
    udid: str,
    *,
    backend: str = AX,
    match: str | None = None,
    match_key: str = "AXLabel",
) -> list[str]:
    """Argv for the frontmost application's elements. Never another process's."""
    args = ["ui", "describe-all", "--udid", udid, "--api", backend]
    if match is not None:
        args += ["--match", match, "--match-key", match_key]
    return args


def describe_point_args(udid: str, x: float, y: float) -> list[str]:
    """Argv for a system-wide hit test: whatever is under a point, in whichever process drew it."""
    return ["ui", "describe-point", "--udid", udid, str(int(x)), str(int(y))]


def tap_args(udid: str, x: float, y: float) -> list[str]:
    """Argv for a tap at a point. Not `ui tap MARKER`: the marker form is frontmost-only."""
    return ["ui", "tap", "--udid", udid, str(int(x)), str(int(y))]


def text_args(udid: str, text: str) -> list[str]:
    """Argv for typing into whatever has focus. No keycode table, no hardware-keyboard toggle."""
    return ["ui", "text", "--udid", udid, text]


def disconnect_args(udid: str) -> list[str]:
    """Argv to drop one companion's registration. Never `idb kill`, which SIGKILLs every companion on the Mac."""
    return ["disconnect", udid]


def _element(raw: Any) -> Element | None:
    if not isinstance(raw, dict):
        return None
    frame = raw.get("frame")
    if not isinstance(frame, dict):
        return None
    traits = raw.get("traits")
    try:
        pid = int(raw.get("pid") or 0)
        rect = Frame(
            float(frame.get("x", 0)),
            float(frame.get("y", 0)),
            float(frame.get("width", 0)),
            float(frame.get("height", 0)),
        )
    except (TypeError, ValueError):
        # idb has never printed a non-numeric pid or coordinate, but "output we
        # do not recognize" has to mean nothing on screen at every level, not
        # only at the top: a parser that raises here would turn a shrug into a
        # crash in the middle of a run.
        return None
    return Element(
        pid=pid,
        type=str(raw.get("type") or ""),
        label=raw.get("AXLabel"),
        value=raw.get("AXValue"),
        frame=rect,
        traits=tuple(str(t) for t in traits) if isinstance(traits, list) else (),
    )


def parse_elements(stdout: str) -> list[Element]:
    """`describe-all` output as elements. Anything that is not a JSON list is nothing on screen."""
    try:
        raw = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(raw, list):
        return []
    return [e for e in (_element(r) for r in raw) if e is not None]


def parse_element(stdout: str) -> Element | None:
    """`describe-point` output as one element. A sentence, or nothing there, is None."""
    try:
        raw = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return None
    return _element(raw)
