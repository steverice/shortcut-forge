"""The accessibility API, addressed by process id.

System Events cannot see Device Hub on macOS 27.0 (26A428) with Xcode 27.0
(27A266a): `process "DeviceHub"` resolves to an entry whose unix id is 0 and
which has no windows and no menu bar, whichever of its two names is used,
after a restart of System Events, and however the app was launched. The AX
API reached through `AXUIElementCreateApplication(pid)` reads the same
process fine — windows with titles and frames, the menu bar with its check
marks, and presses that land. So the Device Hub host goes through this
module and never through AppleScript.

The functions are loaded from ApplicationServices at import time, which is
why this module is only imported by the harness and never by anything that
runs on a Mac without the `sim` extra.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, Any, cast

import AppKit as _AppKit
import Foundation as _Foundation
import objc as _objc

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

# pyobjc populates these namespaces lazily and ships no stubs; one cast each
# rather than an ignore on every use.
AppKit = cast("Any", _AppKit)
Foundation = cast("Any", _Foundation)
objc = cast("Any", _objc)

_BUNDLE = Foundation.NSBundle.bundleWithPath_("/System/Library/Frameworks/ApplicationServices.framework")
_FN: dict[str, Any] = {}
# AX elements and values are CF types, which the bridge carries as objects.
objc.loadBundleFunctions(
    _BUNDLE,
    _FN,
    [
        ("AXUIElementCreateApplication", b"@i"),
        ("AXUIElementCopyAttributeValue", b"i@@o^@"),
        ("AXUIElementPerformAction", b"i@@"),
        ("AXValueGetValue", b"B@io^{CGPoint=dd}"),
    ],
)
_SIZE: dict[str, Any] = {}
objc.loadBundleFunctions(_BUNDLE, _SIZE, [("AXValueGetValue", b"B@io^{CGSize=dd}")])

_POINT_TYPE, _SIZE_TYPE = 1, 2
_ACTIVATE_IGNORING_OTHER_APPS = 1 << 1


def attribute(element: Any, name: str) -> Any:
    """One attribute of an element, or None when it is absent or unreadable."""
    err, value = _FN["AXUIElementCopyAttributeValue"](element, name, None)
    return value if err == 0 else None


def press(element: Any) -> bool:
    return _FN["AXUIElementPerformAction"](element, "AXPress") == 0


def raise_window(element: Any) -> bool:
    return _FN["AXUIElementPerformAction"](element, "AXRaise") == 0


class App:
    """One running application, read through the accessibility tree."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.element = _FN["AXUIElementCreateApplication"](pid)

    @classmethod
    def running(cls, app: Path) -> App | None:
        """The process running this app bundle's executable, or None.

        By executable path rather than bundle id: a Mac with Xcode and an
        Xcode beta has two Device Hubs with one bundle id, and with both
        running LaunchServices answers a bundle-id lookup with a process id
        of -1. The path names the one that belongs to the selected Xcode.
        """
        r = subprocess.run(["pgrep", "-f", str(app / "Contents" / "MacOS")], capture_output=True, text=True)
        pids = [int(p) for p in r.stdout.split()]
        return cls(pids[0]) if pids else None

    def activate(self) -> None:
        app = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(self.pid)
        if app is not None:
            app.activateWithOptions_(_ACTIVATE_IGNORING_OTHER_APPS)

    # -- windows --------------------------------------------------------
    def windows(self) -> list[tuple[str, Any]]:
        """(title, element) for every window, in the order the app reports them."""
        return [(str(attribute(w, "AXTitle") or ""), w) for w in attribute(self.element, "AXWindows") or []]

    def front_title(self) -> str:
        # Read off the window list rather than AXFocusedWindow: the element
        # that attribute returns answers AXTitle with nothing, while the same
        # window listed under AXWindows carries its title.
        windows = self.windows()
        main = [title for title, w in windows if attribute(w, "AXMain")]
        return main[0] if main else (windows[0][0] if windows else "")

    @staticmethod
    def frame(window: Any) -> tuple[int, int, int, int]:
        """(x, y, width, height) in screen points."""
        _ok, pos = _FN["AXValueGetValue"](attribute(window, "AXPosition"), _POINT_TYPE, None)
        _ok, size = _SIZE["AXValueGetValue"](attribute(window, "AXSize"), _SIZE_TYPE, None)
        return int(pos.x), int(pos.y), int(size.width), int(size.height)

    # -- menus ----------------------------------------------------------
    def menu_item(self, path: Sequence[str]) -> Any | None:
        """The element at `path` through the menu bar, or None when a step is missing."""
        element = attribute(self.element, "AXMenuBar")
        for title in path:
            if element is None:
                return None
            children = cast("list[Any]", attribute(element, "AXChildren") or [])
            # A menu bar item or a submenu item holds one AXMenu, whose
            # children are the entries; step through it.
            if len(children) == 1 and attribute(children[0], "AXRole") == "AXMenu":
                children = cast("list[Any]", attribute(children[0], "AXChildren") or [])
            element = next((c for c in children if attribute(c, "AXTitle") == title), None)
        return element

    @staticmethod
    def mark(item: Any) -> str | None:
        """The check mark on a menu item, or None when it carries none."""
        value = attribute(item, "AXMenuItemMarkChar")
        return None if value in (None, "") else str(value)
