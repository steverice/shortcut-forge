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


def test_is_button_is_the_type_because_the_trait_does_not_track_it():
    """Measured across every capture: 183 elements are buttons carrying no `Button` trait.

    The axbridge tree's Play buttons are the bulk of them, and no element
    anywhere carries the trait without also being typed a button — so the type
    is the signal and the trait would only lose buttons.
    """
    els = idb.parse_elements(capture("library", "all-axbridge.json"))
    buttons = [e for e in els if e.is_button]
    assert buttons
    assert any("Button" not in e.traits for e in buttons), "a trait-based check would have missed these"
    assert all(e.type == "Button" for e in buttons)
    assert not idb.Element(1, "Cell", "Attendance", None, idb.Frame(0, 0, 10, 10), ("Button",)).is_button


def test_the_keyboards_keys_are_told_apart_by_trait_not_by_label():
    """A numeric keypad has no letters, and the prompt this harness answers is six digits.

    The default backend types the keys as `Button`, exactly like a sheet's own
    buttons, and marks them with a `KeyboardKey` trait; `axbridge` types them
    `Key` and gives them no traits. Nothing outside a keyboard carries the
    trait in any capture.
    """
    ax = idb.parse_elements(capture("setup-question-keyboard", "all-ax.json"))
    keys = [e for e in ax if "KeyboardKey" in e.traits]
    assert len(keys) == 33
    assert {"delete", "Dictate", "Emoji"} <= {e.label for e in keys}, "not only letters"
    assert all(e.type == "Button" for e in keys), "the same type as the sheet's own buttons"
    assert "KeyboardKey" not in next(e for e in ax if e.label == "Close").traits


def test_output_that_is_not_the_shape_we_expect_is_nothing_on_screen():
    """Every level of it, not only the top: a parser that raises here crashes a run."""
    assert idb.parse_elements('{"type": "Application"}') == []
    assert idb.parse_element('{"type": "Button", "AXLabel": "Done"}') is None
    assert idb.parse_element('{"type":"Button","frame":{"x":"n/a","y":0,"width":1,"height":1}}') is None
    assert idb.parse_elements('[{"type":"Button"},{"type":"Button","frame":{"x":0,"y":0,"width":2,"height":2}}]') == [
        idb.Element(0, "Button", None, None, idb.Frame(0, 0, 2, 2), ())
    ]
