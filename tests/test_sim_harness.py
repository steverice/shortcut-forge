"""The harness's pure parts: geometry, title matching, archive parsing, and button finding.

Nothing here boots a simulator or calls `xcode-select`; importing the module
must not either.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
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

typed = pathlib.Path(os.environ["IDB_LOG"] + ".typed")
after = os.environ.get("IDB_SCENE_AFTER")
if after and typed.exists():
    scene = pathlib.Path(after)
if argv[:2] == ["ui", "text"]:
    typed.write_text("typed")
    sys.exit(0)
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


def test_a_tree_match_the_hit_test_disagrees_with_is_not_found(fake_idb, monkeypatch):
    """The rule the whole finder rests on, exercised rather than reasoned about.

    A runner dialog is drawn by another process, so the frontmost tree still
    lists the screen underneath it — that screen's buttons included. Only a hit
    test knows what is actually on top at a given point. Here the tree offers
    *Add Shortcut* and every hit test answers with a dialog's *Allow*, which is
    what a consent sheet over the setup page looks like from outside.
    """
    sim = fake_idb("setup-question")
    covering = idb.Element(99, "Button", "Allow", None, idb.Frame(0, 700, 402, 60), ("Button",))
    monkeypatch.setattr(Simulator, "at", lambda self, x, y: covering)
    assert sim.find_button("Add Shortcut") is None


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


def test_clear_prompts_presses_until_none_is_left_and_says_what_it_pressed(fake_idb, monkeypatch):
    sim = fake_idb("library")
    answers = iter([button("Always Allow"), button("Allow"), None])
    monkeypatch.setattr(Simulator, "find_button", lambda self, *labels: next(answers, None))
    monkeypatch.setattr(Simulator, "_tap", lambda self, e, **kw: e.label or "")
    assert sim.clear_prompts() == ["Always Allow", "Allow"]


def test_clear_prompts_never_offers_allow_once_or_done(fake_idb, monkeypatch):
    """Allow Once asks again next run; Done would submit an empty answer to the Ask dialog."""
    sim = fake_idb("library")
    asked = []

    def find(self, *labels):
        asked.append(labels)
        return

    monkeypatch.setattr(Simulator, "find_button", find)
    assert sim.clear_prompts() == []
    assert asked == [("Always Allow", "Allow")]


def test_clear_prompts_raises_if_a_consent_never_stops_coming_back(fake_idb, monkeypatch):
    sim = fake_idb("library")
    monkeypatch.setattr(Simulator, "find_button", lambda self, *labels: button("Allow"))
    monkeypatch.setattr(Simulator, "_tap", lambda self, e, **kw: e.label or "")
    with pytest.raises(harness.SimulatorError, match="kept coming back"):
        sim.clear_prompts()


def test_fill_types_into_the_field_and_reads_it_back(fake_idb, monkeypatch):
    sim = fake_idb("setup-question")
    typed = next(
        e.value
        for e in idb.parse_elements((FIXTURES / "setup-question-keyboard" / "all-ax.json").read_text())
        if e.type in harness.FIELD_TYPES and e.value
    )
    monkeypatch.setenv("IDB_SCENE_AFTER", str(FIXTURES / "setup-question-keyboard"))
    assert sim.fill(typed) == typed


def test_a_typed_value_that_arrives_late_is_waited_for(fake_idb, monkeypatch):
    """`idb ui text` returns before the field's `AXValue` has caught up.

    Measured during the fixture capture: a read 1.5 s after typing came back
    one character short, on a device that had read the same string verbatim
    earlier in the session. A single read after a fixed sleep is a race, and
    the read-back is the whole reason for typing through `fill` rather than
    calling `idb ui text` directly.
    """
    sim = fake_idb("setup-question")
    field = idb.Frame(23, 237, 356, 100)
    readings = iter(
        [
            idb.Element(9, "TextArea", None, "zz424", field, ()),
            idb.Element(9, "TextArea", None, "zz42424", field, ()),
            idb.Element(9, "TextArea", None, "zz424242", field, ()),
        ]
    )
    monkeypatch.setattr(Simulator, "_field_in_tree", lambda self: next(readings, None))
    assert sim._settle_value(sim._field_in_tree, "zz424242") == "zz424242"


def test_a_value_that_never_settles_comes_back_as_whatever_it_holds(fake_idb, monkeypatch):
    """So the caller raises with what the field actually shows, not with a timeout."""
    sim = fake_idb("setup-question")
    stuck = idb.Element(9, "TextArea", None, "zz424", idb.Frame(23, 237, 356, 100), ())
    monkeypatch.setattr(Simulator, "_field_in_tree", lambda self: stuck)
    assert sim._settle_value(sim._field_in_tree, "zz424242", timeout=0.01) == "zz424"


def test_fill_raises_when_the_field_did_not_take_the_text(fake_idb):
    """The read-back the old harness never had: a tap that missed the field typed into nothing."""
    sim = fake_idb("setup-question")
    with pytest.raises(harness.SimulatorError, match="holds"):
        sim.fill("424242")


def test_an_empty_answer_is_not_read_back_because_an_empty_field_shows_its_placeholder(fake_idb):
    """`AXValue` carries the placeholder when a field has no content.

    Measured: an untouched Ask field and an untouched setup-question field both
    report `AXValue` of "Text", and neither carries a separate placeholder
    attribute to tell it apart from real content. Comparing that against "" would
    wait out the whole timeout and then raise about a field that is behaving
    normally — and one consumer test submits an empty answer deliberately.
    """
    sim = fake_idb("setup-question")
    assert sim.fill("") == ""
    assert not any(c.startswith("ui text") for c in fake_idb.log())


def test_confirm_presses_the_label_when_nothing_covers_it(fake_idb):
    sim = fake_idb("setup-question")
    assert sim.confirm("Add Shortcut") == "Add Shortcut"


def test_confirm_clears_the_keyboard_and_never_taps_blind(fake_idb, monkeypatch):
    """On this sheet the empty area is the dimmed backdrop, which dismisses it and loses the answer."""
    sim = fake_idb("setup-question-keyboard")
    pressed = []
    monkeypatch.setattr(Simulator, "_tap", lambda self, e, **kw: (pressed.append(e.label), e.label or "")[1])
    with pytest.raises(harness.SimulatorError, match="Add Shortcut"):
        sim.confirm("Add Shortcut")
    assert set(pressed) <= {"Close", "Continue"}, f"confirm tapped something it should not have: {pressed}"


def test_prepare_refuses_a_companion_that_may_be_from_another_xcode(fake_idb, monkeypatch):
    sim = fake_idb("library")
    monkeypatch.setenv("DEVELOPER_DIR", "/Applications/Xcode-beta.app/Contents/Developer")
    monkeypatch.setattr(Simulator, "_companion_running", lambda self: True)
    with pytest.raises(harness.SimulatorError, match="drop_companion"):
        sim.prepare()


def test_preparing_a_device_that_is_not_booted_never_opens_device_hub(fake_idb, monkeypatch):
    """Quitting Device Hub shuts down every booted simulator, so the harness never opens it."""
    sim = fake_idb("library")
    calls: list[str] = []
    monkeypatch.setattr(Simulator, "_is_booted", lambda self: False)
    monkeypatch.setattr(Simulator, "wait_booted", lambda self, timeout=180: calls.append("wait"))
    monkeypatch.setattr(
        harness,
        "_run",
        lambda *args, **kw: calls.append(" ".join(args)) or subprocess.CompletedProcess(args, 0, "", ""),
    )
    # `raising=False` so this test outlives the task that deletes `host()`.
    monkeypatch.setattr(harness, "host", lambda: pytest.fail("prepare() must never open Device Hub"), raising=False)
    sim.prepare()
    assert any(c.startswith("xcrun simctl boot") for c in calls)
    assert "wait" in calls


def test_install_returns_false_when_the_shortcut_is_already_there(fake_idb, monkeypatch, tmp_path):
    """The library is keyed by filename, and a second import of the same name silently does nothing.

    Reporting that as an install would tell a caller its build had landed when
    the device still holds the old one.
    """
    sim = fake_idb("library")
    monkeypatch.setattr(Simulator, "library", lambda self: ["Attendance"])
    assert sim.install(tmp_path / "Attendance.shortcut") is False
    assert not fake_idb.log(), "it should not have opened anything"


def test_install_stops_at_the_question_page_when_asked_to(fake_idb, monkeypatch, tmp_path):
    """`skip_setup=False` hands the sheet to `fill` and `confirm`, before the name reaches the library.

    That is the shape the setup canary needs: the answer has to be typed while
    the sheet is still up, and the shortcut is not installed until it is.

    Only the `xcrun` open is faked here; idb calls fall through to the real
    `_run` so the fake idb binary on PATH still answers from the fixture.
    Faking `_run` wholesale (as the first draft of this test did) starves
    `_field_in_tree()` of every read, since it shares the same `_run`, and the
    test then spun for the full real-time timeout before failing.
    """
    sim = fake_idb("setup-question")
    real_run = harness._run
    monkeypatch.setattr(
        harness,
        "_run",
        lambda *args, **kw: (
            subprocess.CompletedProcess(args, 0, "", "") if args[0] == "xcrun" else real_run(*args, **kw)
        ),
    )
    assert sim.install(tmp_path / "Setup Canary.shortcut", skip_setup=False) is True
