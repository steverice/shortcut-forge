"""Every check passes on a real build and fails on the mutation it exists to catch."""

from __future__ import annotations

import copy
import itertools

import pytest

from shortcut_forge_lib.actions import ActionList
from shortcut_forge_lib.checks import (
    CheckError,
    check_all,
    check_control_flow,
    check_dictionary_keys,
    check_no_dangling_references,
    check_offsets,
    check_runs_shortcut,
    check_utf16_safe,
)
from shortcut_forge_lib.plist import OBJ, act, dict_field, dict_key, document, kv, out, text_value, ts, var


def actions(doc):
    return doc["WFWorkflowActions"]


def prompt_token(doc):
    """The first text token with an attachment in it."""
    for a in actions(doc):
        token = a["WFWorkflowActionParameters"].get("WFTextActionText")
        if isinstance(token, dict) and OBJ in token["Value"]["string"]:
            return token
    raise AssertionError("no token with attachments")


# -- the real builds pass -----------------------------------------------------


def test_every_real_build_passes_check_all(
    attendance, check_in, car_greetings, car_greetings_trigger, publisher_baseline
):
    for doc in (attendance, check_in, car_greetings, car_greetings_trigger, publisher_baseline):
        check_all(doc)
    check_all(check_in, known_shortcuts=["Brightwheel Attendance"])
    check_all(car_greetings_trigger, known_shortcuts=["Car Greetings"])


# -- offsets ----------------------------------------------------------------


def test_offset_invariant_catches_a_shifted_prompt(car_greetings):
    doc = copy.deepcopy(car_greetings)
    token = prompt_token(doc)
    token["Value"]["string"] = "x" + token["Value"]["string"]
    with pytest.raises(CheckError, match="offset"):
        check_offsets(doc)


def test_offset_invariant_catches_a_lost_placeholder(car_greetings):
    doc = copy.deepcopy(car_greetings)
    token = prompt_token(doc)
    token["Value"]["string"] = token["Value"]["string"].replace(OBJ, "", 1)
    with pytest.raises(CheckError, match="placeholder characters"):
        check_offsets(doc)


def test_offsets_are_checked_inside_nested_parameters():
    """A token buried in a header dictionary is still a token."""
    doc = {
        "WFWorkflowActions": [
            act("is.workflow.actions.downloadurl", WFHTTPHeaders=dict_field([kv("X", ts("a", var("V")))]))
        ]
    }
    check_offsets(doc)
    doc["WFWorkflowActions"][0]["WFWorkflowActionParameters"]["WFHTTPHeaders"]["Value"]["WFDictionaryFieldValueItems"][
        0
    ]["WFValue"]["Value"]["string"] = "ab"
    with pytest.raises(CheckError, match="placeholder"):
        check_offsets(doc)


# -- utf-16 -------------------------------------------------------------------


def test_non_bmp_character_next_to_an_attachment_is_caught(car_greetings):
    doc = copy.deepcopy(car_greetings)
    prompt_token(doc)["Value"]["string"] += "\U0001f6eb"
    with pytest.raises(CheckError, match="UTF-16"):
        check_utf16_safe(doc)


def test_non_bmp_character_in_plain_text_is_allowed():
    """Nothing to misplace: no attachments, no offsets."""
    check_utf16_safe({"WFWorkflowActions": [act("is.workflow.actions.gettext", WFTextActionText=ts("\U0001f6eb"))]})


def test_bmp_symbols_are_fine():
    check_utf16_safe({"WFWorkflowActions": [act("x", WFTextActionText=ts("✅ ", var("Name"), " ⚠️ —"))]})


# -- references ---------------------------------------------------------------


def test_dangling_output_reference_is_caught(car_greetings):
    doc = copy.deepcopy(car_greetings)
    prompt_token(doc)["Value"]["attachmentsByRange"]["{78, 1}"]["OutputUUID"] = "NOPE"
    with pytest.raises(CheckError, match="dangling"):
        check_no_dangling_references(doc)


# -- dictionary keys ----------------------------------------------------------


def test_unknown_dictionary_key_is_caught():
    dictionary = act(
        "is.workflow.actions.dictionary", UUID="D", WFItems=dict_field([kv("driver_name", text_value("Daddy"))])
    )
    good = {
        "WFWorkflowActions": [dictionary, act("x", WFTextActionText=ts(dict_key("D", "Dictionary", "driver_name")))]
    }
    check_dictionary_keys(good)
    bad = {"WFWorkflowActions": [dictionary, act("x", WFTextActionText=ts(dict_key("D", "Dictionary", "captain")))]}
    with pytest.raises(CheckError, match="captain"):
        check_dictionary_keys(bad)


# -- control flow -------------------------------------------------------------


def flow(*markers):
    """Markers as (identifier-short-name, group, mode, extra) tuples."""
    names = {
        "if": "is.workflow.actions.conditional",
        "each": "is.workflow.actions.repeat.each",
        "count": "is.workflow.actions.repeat.count",
        "menu": "is.workflow.actions.choosefrommenu",
    }
    acts = []
    for kind, group, mode, *extra in markers:
        params = {"GroupingIdentifier": group, "WFControlFlowMode": mode, **(extra[0] if extra else {})}
        acts.append(act(names[kind], **params))
    return {"WFWorkflowActions": acts}


def test_a_repeat_closed_by_a_conditional_is_caught():
    """The bug from brightwheel's ARCHITECTURE.md: the body never runs and nothing says so."""
    with pytest.raises(CheckError, match=r"closed by is\.workflow\.actions\.conditional"):
        check_control_flow(flow(("each", "G", 0), ("if", "G", 2)))


def test_balanced_nesting_passes():
    check_control_flow(flow(("count", "A", 0), ("if", "B", 0), ("if", "B", 1), ("if", "B", 2), ("count", "A", 2)))


def test_interleaved_groups_are_caught():
    with pytest.raises(CheckError, match="still open"):
        check_control_flow(flow(("count", "A", 0), ("if", "B", 0), ("count", "A", 2), ("if", "B", 2)))


def test_group_opened_twice_is_caught():
    with pytest.raises(CheckError, match="opened twice"):
        check_control_flow(flow(("if", "G", 0), ("if", "G", 0), ("if", "G", 2), ("if", "G", 2)))


def test_unclosed_group_is_caught():
    with pytest.raises(CheckError, match="never closed"):
        check_control_flow(flow(("if", "G", 0)))


def test_close_without_open_is_caught():
    with pytest.raises(CheckError, match="never opened"):
        check_control_flow(flow(("if", "G", 2)))


def test_else_on_a_repeat_is_caught():
    with pytest.raises(CheckError, match="no middle marker"):
        check_control_flow(flow(("each", "G", 0), ("each", "G", 1), ("each", "G", 2)))


def test_else_for_a_group_that_is_not_innermost_is_caught():
    with pytest.raises(CheckError, match="while group B is open"):
        check_control_flow(flow(("if", "A", 0), ("if", "B", 0), ("if", "A", 1), ("if", "B", 2), ("if", "A", 2)))


def test_menu_items_must_match_cases_exactly():
    good = flow(
        ("menu", "M", 0, {"WFMenuItems": ["In", "Out"]}),
        ("menu", "M", 1, {"WFMenuItemTitle": "In"}),
        ("menu", "M", 1, {"WFMenuItemTitle": "Out"}),
        ("menu", "M", 2),
    )
    check_control_flow(good)
    reordered = flow(
        ("menu", "M", 0, {"WFMenuItems": ["In", "Out"]}),
        ("menu", "M", 1, {"WFMenuItemTitle": "Out"}),
        ("menu", "M", 1, {"WFMenuItemTitle": "In"}),
        ("menu", "M", 2),
    )
    with pytest.raises(CheckError, match="do not match its cases"):
        check_control_flow(reordered)
    missing = flow(
        ("menu", "M", 0, {"WFMenuItems": ["In", "Out"]}),
        ("menu", "M", 1, {"WFMenuItemTitle": "In"}),
        ("menu", "M", 2),
    )
    with pytest.raises(CheckError, match="do not match its cases"):
        check_control_flow(missing)


def test_unknown_mode_is_caught():
    with pytest.raises(CheckError, match="unknown control-flow mode"):
        check_control_flow(flow(("if", "G", 7)))


def test_the_helpers_cannot_produce_a_mismatched_close():
    a = ActionList(f"U{n}" for n in itertools.count())
    a.repeat_each_open("G", out("L", "X"))
    a.repeat_each_close("G")
    check_control_flow({"WFWorkflowActions": list(a)})


# -- run shortcut -------------------------------------------------------------


def test_wrapper_naming_a_missing_shortcut_is_caught(car_greetings_trigger):
    check_runs_shortcut(car_greetings_trigger, ["Car Greetings"])
    with pytest.raises(CheckError, match="resolves by name"):
        check_runs_shortcut(car_greetings_trigger, ["Some Other Name"])


def test_wrapper_with_disagreeing_names_is_caught(car_greetings_trigger):
    doc = copy.deepcopy(car_greetings_trigger)
    run = next(a for a in actions(doc) if a["WFWorkflowActionIdentifier"] == "is.workflow.actions.runworkflow")
    run["WFWorkflowActionParameters"]["WFWorkflow"]["workflowName"] = "Other"
    with pytest.raises(CheckError, match="outside and 'Other' inside"):
        check_runs_shortcut(doc, ["Car Greetings", "Other"])


def test_check_all_skips_run_shortcut_unless_asked(car_greetings_trigger):
    check_all(car_greetings_trigger)
    with pytest.raises(CheckError):
        check_all(car_greetings_trigger, known_shortcuts=[])


def test_check_all_on_an_empty_document():
    check_all(document("N", [], glyph=1, color=2))
