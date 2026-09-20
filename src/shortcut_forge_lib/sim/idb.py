"""Argument construction and JSON parsing for the `idb` CLI.

idb (https://github.com/facebook/idb) injects touches into a booted simulator
through a companion process of its own, so nothing here needs a window, a
screen mapping, or Accessibility permission. Install is `brew trust facebook/fb`
and then `brew install facebook/fb/idb`; Homebrew refuses the formula from an
untrusted tap.

Only argv shapes and parsing live here, the way `guest/tart.py` holds tart's,
so both are tested against captured output with no simulator. Three of the
choices below are measurements rather than preferences:

  * **`--api` is always passed.** The two backends see different screens: the
    default lists a presented sheet — the setup question's heading, its field,
    *Add Shortcut*, *Skip Setup* — and the `axbridge` backend lists the
    navigation bar and each tile's Play button, 80 elements to the default's 8
    on the library screen, without the sheet. idb's unspecified default is
    assumed to be `ax` and not relied on.
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
        """A button by type, or by trait — axbridge reports a library tile as a `Cell` carrying one."""
        return self.type == "Button" or "Button" in self.traits


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
    return Element(
        pid=int(raw.get("pid") or 0),
        type=str(raw.get("type") or ""),
        label=raw.get("AXLabel"),
        value=raw.get("AXValue"),
        frame=Frame(
            float(frame.get("x", 0)),
            float(frame.get("y", 0)),
            float(frame.get("width", 0)),
            float(frame.get("height", 0)),
        ),
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
