"""The harness's pure parts: geometry, title matching, archive parsing, and button finding.

Nothing here boots a simulator or calls `xcode-select`; importing the module
must not either.
"""

from __future__ import annotations

import plistlib

import numpy as np
import pytest
from PIL import Image

from shortcut_forge.sim import harness
from shortcut_forge.sim.harness import (
    KEYCODES,
    Simulator,
    _contiguous,
    _keyed_lookup,
    _plist,
    _screen_box,
    _title_is,
    _widest_gap,
)


def test_importing_the_module_does_not_detect_a_host():
    assert harness._HOST is None


def test_title_matching_needs_a_boundary():
    """`bw-ios27rc` is a prefix of `bw-ios27rc-b`; a loose match drives the wrong device."""
    assert _title_is("iPhone 17 Pro \u2013 iOS 27.0", "iPhone 17 Pro")
    assert _title_is("iPhone 17 Pro \u2013 iOS 27.0", "iPhone 17 Pro", "27.0")
    assert not _title_is("bw-ios27rc-b \u2013 iOS 27.0", "bw-ios27rc")
    assert not _title_is("iPhone 17 Pro \u2013 iOS 26.5", "iPhone 17 Pro", "27.0")
    assert _title_is("iPhone 17 Pro", "iPhone 17 Pro")


def test_contiguous_splits_on_gaps():
    assert _contiguous([1, 2, 3, 7, 8, 20]) == [[1, 2, 3], [7, 8], [20]]
    assert _contiguous([1, 3, 5], gap=2) == [[1, 3, 5]]
    assert _contiguous([]) == []


def test_widest_gap_is_between_runs():
    assert _widest_gap([[0, 1], [5, 6], [20, 21]]) == (7, 19)
    assert _widest_gap([[0, 1]]) is None


def test_screen_box_finds_the_gap_between_two_dark_walls():
    """A synthetic Device Hub window: dark bezel walls around a bright screen."""
    h, w = 400, 300
    dark = np.zeros((h, w), dtype=bool)
    dark[:, :40] = True  # left wall
    dark[:, 260:] = True  # right wall
    dark[:50, :] = True  # top wall
    dark[350:, :] = True  # bottom wall
    dark[100:110, 120:130] = True  # a dark patch in the wallpaper, inside the screen
    assert _screen_box(dark) == (40, 50, 259, 349)


def test_screen_box_with_no_walls_is_none():
    assert _screen_box(np.zeros((100, 100), dtype=bool)) is None


def keyed_archive(objects):
    """An NSKeyedArchiver-shaped plist: $objects with UID references."""
    return plistlib.dumps({"$objects": objects, "$top": {"root": plistlib.UID(1)}}, fmt=plistlib.FMT_BINARY)


def test_keyed_lookup_resolves_uids_and_skips_null():
    archive = plistlib.loads(keyed_archive(["$null", {"NS.string": plistlib.UID(2)}, "the value"]))
    assert _keyed_lookup(archive, "NS.string") == "the value"
    assert _keyed_lookup(archive, "missing", "NS.string") == "the value"
    assert _keyed_lookup(archive, "missing") is None
    assert _keyed_lookup({"not": "an archive"}, "NS.string") is None
    bytes_archive = plistlib.loads(keyed_archive(["$null", {"object": b"raw bytes"}]))
    assert _keyed_lookup(bytes_archive, "object") == "raw bytes"


def test_plist_strips_the_version_byte_and_tolerates_junk():
    raw = plistlib.dumps({"a": 1})
    assert _plist(raw) == {"a": 1}
    assert _plist(b"\x01" + raw) == {"a": 1}
    assert _plist(b"not a plist") is None
    assert _plist(None) is None


def blue_screenshot(buttons, size=(1206, 2622)):
    """A device screenshot with iOS-blue rectangles where `buttons` says."""
    w, h = size
    img = np.full((h, w, 3), 255, dtype=np.uint8)
    for x0, y0, x1, y1 in buttons:
        img[y0:y1, x0:x1] = (0, 122, 255)
    return Image.fromarray(img)


def test_blue_buttons_finds_wide_blue_rectangles_bottom_last():
    sim = Simulator("FAKE")
    img = blue_screenshot([(100, 2300, 1100, 2400), (100, 2450, 1100, 2550)])
    boxes = sim.blue_buttons(img)
    assert len(boxes) == 2
    assert boxes[0][1] < boxes[1][1]
    _x0, y0, _x1, y1 = max(boxes, key=lambda b: (b[3], b[2]))
    assert 2440 <= y0 <= 2460
    assert 2540 <= y1 <= 2560


def test_blue_buttons_ignores_the_tall_shortcut_tile_and_narrow_shapes():
    sim = Simulator("FAKE")
    img = blue_screenshot([(400, 800, 800, 1300), (100, 2300, 300, 2400)])  # tall tile, narrow button
    assert sim.blue_buttons(img) == []


def test_keycodes_cover_digits_and_lowercase():
    for ch in "0123456789abcdefghijklmnopqrstuvwxyz ":
        assert ch in KEYCODES


def test_host_attribute_is_lazy(monkeypatch):
    calls = []
    monkeypatch.setattr(harness, "_detect_host", lambda: calls.append(1) or "detected")
    monkeypatch.setattr(harness, "_HOST", None)
    assert harness.host() == "detected"
    assert harness.host() == "detected"
    assert calls == [1]
    assert harness.HOST == "detected"
    with pytest.raises(AttributeError):
        _ = harness.no_such_name
