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


def test_the_default_backend_is_the_sheet_and_axbridge_is_the_window_behind_it():
    """Measured on iOS 27.0, and the reason the finder asks the default backend first.

    The default backend returns the presented sheet and almost nothing else:
    its heading, its text field, its two buttons. `axbridge` returns the whole
    window hierarchy with the library underneath — twenty-odd times the
    elements — and does not report the field at all, which is why
    `_field_in_tree` reads the default tree.
    """
    ax = idb.parse_elements(capture("setup-question", "all-ax.json"))
    bridge = idb.parse_elements(capture("setup-question", "all-axbridge.json"))
    assert {"Add Shortcut", "Skip Setup"} <= {e.label for e in ax}
    assert any(e.type in ("TextField", "TextArea") for e in ax)
    assert len(ax) * 10 < len(bridge), f"the default backend should be the small tree: {len(ax)} vs {len(bridge)}"
    assert not any(e.type in ("TextField", "TextArea") for e in bridge)


def test_the_keyboard_hides_the_sheets_buttons_from_the_default_backend():
    """Why `confirm` clears the keyboard before it looks for a button to press.

    With the keyboard up, the default backend's tree is the field, the
    keyboard's own Close, a predictive-text suggestion and thirty-seven letter
    keys — and neither of the sheet's buttons. The keys arrive as ordinary
    `Button`s with single-character labels here; `axbridge` types the same keys
    as `Key`. Anything that recognizes the keyboard has to accept both.
    """
    ax = idb.parse_elements(capture("setup-question-keyboard", "all-ax.json"))
    bridge = idb.parse_elements(capture("setup-question-keyboard", "all-axbridge.json"))
    assert "Add Shortcut" not in {e.label for e in ax}
    assert "Close" in {e.label for e in ax}
    assert sum(1 for e in ax if e.label in ("q", "w", "e", "r", "t", "y")) == 6
    assert not any(e.type == "Key" for e in ax)
    assert any(e.type == "Key" for e in bridge)


def test_center_is_the_middle_of_the_frame():
    assert idb.Frame(23, 252, 172, 54).center() == (109, 279)


def test_a_cell_carrying_a_button_trait_counts_as_a_button():
    """axbridge reports a library tile that way."""
    cell = idb.Element(1, "Cell", "Attendance", None, idb.Frame(0, 0, 10, 10), ("Button", "Scrollable"))
    assert cell.is_button
    assert not idb.Element(1, "Key", "Done", None, idb.Frame(0, 0, 10, 10), ()).is_button
