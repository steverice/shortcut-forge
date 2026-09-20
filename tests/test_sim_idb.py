"""`sim/idb.py`: the argv shapes, and parsing against output captured from a device.

Nothing here runs idb. The captures in `fixtures/idb/` are what a booted iOS
27.0 device actually printed; see that directory's README for when and from
what.
"""

from __future__ import annotations

from pathlib import Path

from shortcut_forge_lib.sim import idb

FIXTURES = Path(__file__).parent / "fixtures" / "idb"
UDID = "F00DCAFE-0000-0000-0000-000000000000"


def capture(scene: str, name: str) -> str:
    return (FIXTURES / scene / name).read_text()


# -- argv -----------------------------------------------------------------------


def test_describe_all_names_the_backend_explicitly():
    """idb's unspecified backend is not relied on: every measurement used --api."""
    assert idb.describe_all_args(UDID) == ["ui", "describe-all", "--udid", UDID, "--api", "ax"]
    assert idb.describe_all_args(UDID, backend=idb.AXBRIDGE)[-1] == "axbridge"


def test_describe_all_can_ask_for_one_label():
    assert idb.describe_all_args(UDID, match="Add Shortcut") == [
        "ui",
        "describe-all",
        "--udid",
        UDID,
        "--api",
        "ax",
        "--match",
        "Add Shortcut",
        "--match-key",
        "AXLabel",
    ]


def test_the_rest_of_the_argv():
    assert idb.describe_point_args(UDID, 201, 279) == ["ui", "describe-point", "--udid", UDID, "201", "279"]
    assert idb.tap_args(UDID, 201.6, 279.4) == ["ui", "tap", "--udid", UDID, "201", "279"]
    assert idb.text_args(UDID, "012345") == ["ui", "text", "--udid", UDID, "012345"]
    assert idb.disconnect_args(UDID) == ["disconnect", UDID]


# -- parsing --------------------------------------------------------------------


def test_the_library_capture_parses_into_elements_with_point_frames():
    elements = idb.parse_elements(capture("library", "all-ax.json"))
    app = elements[0]
    assert app.type == "Application"
    assert app.pid > 0
    assert (app.frame.x, app.frame.y) == (0, 0)
    assert app.frame.width < 1000, "frames are device points, not pixels"
    assert any(e.is_button and e.label for e in elements)


def test_a_sentence_is_nothing_on_screen_rather_than_an_error():
    """Both a warming-up companion and an empty hit test print this."""
    assert idb.parse_elements((FIXTURES / "warmup.txt").read_text()) == []
    assert idb.parse_elements("") == []
    assert idb.parse_element((FIXTURES / "warmup.txt").read_text()) is None
    assert idb.parse_element("") is None


def test_a_point_capture_parses_into_one_element():
    text = next((FIXTURES / "setup-question").glob("point-*.json")).read_text()
    e = idb.parse_element(text)
    assert e is not None
    assert e.frame.width > 0


def test_only_one_backend_sees_the_presented_sheet():
    """The measured limit the finder's fallback order rests on."""
    ax = {e.label for e in idb.parse_elements(capture("setup-question", "all-ax.json"))}
    bridge = {e.label for e in idb.parse_elements(capture("setup-question", "all-axbridge.json"))}
    assert ("Add Shortcut" in ax) != ("Add Shortcut" in bridge)
    assert "Add Shortcut" in ax, (
        "the default backend stopped seeing a presented sheet; the finder asks it first because it does"
    )


def test_center_is_the_middle_of_the_frame():
    assert idb.Frame(23, 252, 172, 54).center() == (109, 279)


def test_a_cell_carrying_a_button_trait_counts_as_a_button():
    """axbridge reports a library tile that way."""
    cell = idb.Element(1, "Cell", "Attendance", None, idb.Frame(0, 0, 10, 10), ("Button", "Scrollable"))
    assert cell.is_button
    assert not idb.Element(1, "Key", "Done", None, idb.Frame(0, 0, 10, 10), ()).is_button
