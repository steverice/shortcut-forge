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

from shortcut_forge_lib.sim import harness, idb
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
    monkeypatch.setattr(Simulator, "screenshot", lambda self, name=None: tmp_path / (name or "shot.png"))

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


def button(label: str, y: float = 300.0) -> idb.Element:
    return idb.Element(99, "Button", label, None, idb.Frame(23, y, 356, 50), ("Button",))


def test_a_dialog_label_is_probed_at_its_seeds_and_never_looked_up_in_the_tree(fake_idb):
    """The screen under a runner dialog has a Done of its own, and so does the keyboard's return key."""
    sim = fake_idb("ask-dialog")
    found = sim.find_button("Done")
    assert found is not None
    assert found.label == "Done"
    trees = [c for c in fake_idb.log() if c.startswith("ui describe-all")]
    assert len(trees) == 1, f"only screen_size() may read the tree for a dialog label, saw {trees}"


def test_a_seed_hit_counts_only_when_the_label_matches_exactly(fake_idb, monkeypatch):
    """The output sheet stacks Allow Once where a two-button sheet puts Allow."""
    sim = fake_idb("library")
    monkeypatch.setattr(Simulator, "at", lambda self, x, y: button("Allow Once"))
    assert sim.find_button("Allow") is None
    assert sim.find_button("Allow Once") is not None


def test_always_allow_is_tried_before_allow(fake_idb):
    """A consent answered with Allow Once asks again on the next run; the persistent choice comes first."""
    labels = [label for label, _fx, _fy in harness.SEEDS]
    missing = [label for label in ("Always Allow", "Allow") if label not in labels]
    assert not missing, (
        f"the capture never saw {missing}: the device had already granted those consents. "
        "Erase it and run tests/capture_idb_fixtures.py again."
    )
    assert labels.index("Always Allow") < labels.index("Allow")


def test_a_tree_match_is_confirmed_by_a_hit_test_before_it_is_returned(fake_idb):
    sim = fake_idb("setup-question")
    found = sim.find_button("Add Shortcut")
    assert found is not None
    assert found.label == "Add Shortcut"
    assert any(c.startswith("ui describe-point") for c in fake_idb.log()), (
        "a tree match must be hit-tested at its center before anything taps it"
    )


def test_tree_trusts_a_single_filtered_hit_without_consulting_axbridge(fake_idb):
    """`_find_in_tree` is the first caller that passes `match=`; a real hit must cost one call."""
    sim = fake_idb("setup-question")
    found = sim._tree(match="Add Shortcut")
    assert any(e.label == "Add Shortcut" for e in found)
    trees = [c for c in fake_idb.log() if c.startswith("ui describe-all")]
    assert len(trees) == 1, f"a filtered hit must cost exactly one describe-all, saw {trees}"


def test_tree_falls_back_to_axbridge_when_the_filtered_read_is_empty(fake_idb, monkeypatch):
    """A filtered read that finds nothing must still try the other backend before giving up.

    The captured fixtures never produce an empty `describe-all`, because every
    scene's default-backend tree includes at least the Application element —
    so this exercises the miss branch directly, at the `elements()` level,
    rather than by asking a fixture to be something it is not.
    """
    sim = fake_idb("setup-question")
    calls: list[str] = []

    def elements(self: Simulator, backend: str = idb.AX, *, match: str | None = None) -> list[idb.Element]:
        calls.append(backend)
        return [] if backend == idb.AX else [button("Elsewhere")]

    monkeypatch.setattr(Simulator, "elements", elements)
    found = sim._tree(match="Add Shortcut")
    assert calls == [idb.AX, idb.AXBRIDGE]
    assert found == [button("Elsewhere")]


def test_a_button_the_keyboard_hides_is_not_found(fake_idb):
    """With the keyboard up the default tree drops the sheet's buttons altogether.

    Measured, not assumed: `find_button` comes back empty because there is
    nothing to find, and the keyboard is what `confirm` has to clear before
    there is. If this ever fails, compare
    `fixtures/idb/setup-question-keyboard/all-ax.json` against
    `setup-question/all-ax.json` — the difference between them is the whole
    reason `confirm` exists.
    """
    sim = fake_idb("setup-question-keyboard")
    assert sim.find_button("Add Shortcut") is None
    assert sim._keyboard_up(sim._tree())


def test_press_taps_the_point_the_hit_test_returned(fake_idb):
    sim = fake_idb("setup-question")
    assert sim.press("Add Shortcut") == "Add Shortcut"
    taps = [c for c in fake_idb.log() if c.startswith("ui tap")]
    assert len(taps) == 1
    x, y = taps[0].split()[-2:]
    assert (FIXTURES / "setup-question" / f"point-{x}-{y}.json").exists(), (
        "the tap landed somewhere no hit test had named"
    )


def test_press_raises_naming_what_was_on_screen(fake_idb):
    sim = fake_idb("library")
    with pytest.raises(harness.SimulatorError, match="Always Allow"):
        sim.press("Always Allow")


def test_a_moving_button_is_not_tapped(fake_idb, monkeypatch):
    """A sheet mid-slide put Allow Once where Always Allow was about to be."""
    sim = fake_idb("library")
    frames = iter([button("Allow", 300.0), button("Allow", 320.0), button("Allow", 340.0), button("Allow", 360.0)])
    monkeypatch.setattr(Simulator, "at", lambda self, x, y: next(frames, None))
    with pytest.raises(harness.SimulatorError, match="moved or vanished"):
        sim._tap(button("Allow", 300.0))
