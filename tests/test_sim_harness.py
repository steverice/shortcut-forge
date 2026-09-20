"""The harness's pure parts: geometry, title matching, archive parsing, and button finding.

Nothing here boots a simulator or calls `xcode-select`; importing the module
must not either.
"""

from __future__ import annotations

import os
import plistlib
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from shortcut_forge_lib.sim import harness
from shortcut_forge_lib.sim.harness import (
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


def _fake_simctl(monkeypatch, reads: list[str]) -> list[tuple[str, ...]]:
    """Every command `set_pasteboard` runs, with `pbpaste` answering from `reads` in turn."""
    calls: list[tuple[str, ...]] = []

    def run(*args: str, check: bool = True, **kw: object) -> object:
        calls.append(args)
        out = reads.pop(0) if args[-2:-1] == ("pbpaste",) else ""
        return type("Done", (), {"stdout": out, "returncode": 0})()

    monkeypatch.setattr(harness, "_run", run)
    monkeypatch.setattr(harness.time, "sleep", lambda _s: None)
    return calls


def test_set_pasteboard_retries_until_the_read_back_agrees(monkeypatch):
    calls = _fake_simctl(monkeypatch, ["the previous value", "123456"])

    Simulator("UDID").set_pasteboard("123456")

    assert [c for c in calls if c[0] == "pbcopy"] == [("pbcopy",), ("pbcopy",)]
    assert ("xcrun", "simctl", "pbsync", "host", "UDID") in calls


def test_set_pasteboard_refuses_a_value_that_never_lands(monkeypatch):
    _fake_simctl(monkeypatch, ["stale"] * 3)

    with pytest.raises(harness.SimulatorError, match="sandboxed"):
        Simulator("UDID").set_pasteboard("123456", attempts=3)


FIXTURES = Path(__file__).parent / "fixtures" / "idb"
UDID = "F00DCAFE-0000-0000-0000-000000000000"

FAKE_IDB = '''#!/usr/bin/env python3
"""A fake idb that answers from a captured scene, by argv rather than by call order."""
import os
import pathlib
import sys

argv = sys.argv[1:]
scene = pathlib.Path(os.environ["IDB_SCENE"])
with pathlib.Path(os.environ["IDB_LOG"]).open("a") as log:
    log.write(" ".join(argv) + "\\n")

always = os.environ.get("IDB_ALWAYS_FAIL")
if always:
    sys.stderr.write(always)
    sys.exit(2)

once = os.environ.get("IDB_FAIL_ONCE")
if once and not pathlib.Path(once).exists():
    pathlib.Path(once).write_text("failed")
    sys.stderr.write("Failed to connect to companion at /tmp/idb/x_companion.sock\\n")
    sys.exit(1)

def answer(path):
    if path.exists():
        sys.stdout.write(path.read_text())
        sys.exit(0)
    sys.stderr.write(
        "No translation object returned for simulator. This means you have likely "
        "specified a point onscreen that is invalid or invisible due to a fullscreen dialog"
    )
    sys.exit(1)

if argv[:2] == ["ui", "describe-all"]:
    backend = argv[argv.index("--api") + 1] if "--api" in argv else "ax"
    answer(scene / f"all-{backend}.json")
if argv[:2] == ["ui", "describe-point"]:
    x, y = [a for a in argv[2:] if a.lstrip("-").isdigit()][:2]
    answer(scene / f"point-{x}-{y}.json")
sys.exit(0)
'''


@pytest.fixture
def fake_idb(tmp_path, monkeypatch):
    """An `idb` on PATH that replays one captured scene, and logs every argv it was given.

    Answering by argv rather than by replay order is deliberate: the finder
    tries seeds in a fixed order, and a test that depended on that order would
    break every time a seed moved.
    """
    binary = tmp_path / "idb"
    binary.write_text(FAKE_IDB)
    binary.chmod(0o755)
    # A `pkill` that matches nothing, which is what it does on a machine with no
    # companion running. Stubbed rather than real so that a unit test cannot
    # signal a process on the developer's machine.
    pkill = tmp_path / "pkill"
    pkill.write_text('#!/bin/sh\necho "pkill $*" >> "$IDB_LOG"\nexit 1\n')
    pkill.chmod(0o755)
    log = tmp_path / "calls.log"
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("IDB_LOG", str(log))
    monkeypatch.setattr(harness.time, "sleep", lambda *_: None)

    def use(scene: str) -> Simulator:
        monkeypatch.setenv("IDB_SCENE", str(FIXTURES / scene))
        return Simulator(UDID)

    use.log = lambda: log.read_text().splitlines() if log.exists() else []
    use.fail_once = lambda: monkeypatch.setenv("IDB_FAIL_ONCE", str(tmp_path / "failed"))
    return use


def test_elements_parses_what_idb_printed(fake_idb):
    sim = fake_idb("library")
    found = sim.elements()
    assert found
    assert found[0].type == "Application"
    assert "--api ax" in " ".join(fake_idb.log())


def test_a_point_with_nothing_there_is_none_not_an_error(fake_idb):
    sim = fake_idb("library")
    assert sim.at(9999, 9999) is None


def test_a_dead_companion_is_disconnected_once_then_retried(fake_idb):
    sim = fake_idb("library")
    fake_idb.fail_once()
    assert sim.elements(), "the retry after a disconnect should have succeeded"
    calls = fake_idb.log()
    assert calls[1] == f"disconnect {UDID}", f"expected a disconnect between the two reads, saw {calls}"
    assert len(calls) == 3


def test_an_unrecognized_failure_raises_with_what_idb_said(fake_idb, monkeypatch):
    sim = fake_idb("library")
    monkeypatch.setenv("IDB_ALWAYS_FAIL", "boom: the companion exited")
    with pytest.raises(harness.SimulatorError, match="boom: the companion exited"):
        sim.elements()


def test_a_companion_that_never_comes_back_raises_after_one_disconnect(fake_idb, monkeypatch):
    """The recovery is tried once. A loop here would hide a companion that cannot start."""
    sim = fake_idb("library")
    monkeypatch.setenv("IDB_ALWAYS_FAIL", "Failed to connect to companion at /tmp/idb/x_companion.sock")
    with pytest.raises(harness.SimulatorError, match="Failed to connect"):
        sim.elements()
    assert fake_idb.log().count(f"disconnect {UDID}") == 1


def test_screen_size_is_the_application_frame_and_is_read_once(fake_idb):
    sim = fake_idb("library")
    w, h = sim.screen_size()
    assert (w, h) == sim.screen_size()
    assert w > 100
    assert h > w
    assert len([c for c in fake_idb.log() if c.startswith("ui describe-all")]) == 1


def test_frontmost_pid_is_the_applications(fake_idb):
    sim = fake_idb("library")
    assert sim.frontmost_pid() == sim.elements()[0].pid


def test_dropping_a_companion_kills_it_and_removes_its_registration(fake_idb):
    """A companion outlives the device it drives and is keyed by UDID alone.

    Both halves are needed: killing the process leaves a registration that the
    next command tries to connect to, and disconnecting alone leaves the
    process running under whichever Xcode spawned it.
    """
    sim = fake_idb("library")
    sim.drop_companion()
    assert fake_idb.log() == [f"pkill -f idb_companion --udid {UDID}", f"disconnect {UDID}"]


def test_dropping_a_companion_never_uses_idb_kill(fake_idb):
    """`idb kill` SIGKILLs every companion on the machine, including another session's."""
    sim = fake_idb("library")
    sim.drop_companion()
    assert not any(c.startswith("kill") for c in fake_idb.log())
