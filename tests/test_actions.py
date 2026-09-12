"""The idioms emit exactly the action shapes the generators emitted by hand."""

from __future__ import annotations

import itertools

import pytest

from shortcut_forge.actions import CLOSE, ELSE, GREATER_THAN, LESS_THAN, OPEN, ActionList
from shortcut_forge.checks import check_control_flow
from shortcut_forge.plist import attach, cond_input, out, ts, var


def counter():
    return (f"U{n}" for n in itertools.count())


def test_uuid_and_the_exposed_iterator_are_the_same_supply():
    a = ActionList(counter())
    assert a.uuid() == "U0"
    assert next(a.uuids) == "U1"
    assert a.uuid() == "U2"


def test_text_and_number():
    a = ActionList(counter())
    u = a.text("in")
    n = a.text(ts(var("X")), name="Copy")
    m = a.number(1)
    assert a[0] == {
        "WFWorkflowActionIdentifier": "is.workflow.actions.gettext",
        "WFWorkflowActionParameters": {"UUID": u, "WFTextActionText": "in"},
    }
    assert a[1]["WFWorkflowActionParameters"] == {
        "UUID": n,
        "CustomOutputName": "Copy",
        "WFTextActionText": ts(var("X")),
    }
    assert a[2]["WFWorkflowActionParameters"] == {"UUID": m, "WFNumberActionNumber": "1"}


def test_count_matches_is_text_then_match_then_count():
    """The exact shape `gate()` produced in brightwheel-checkin."""
    a = ActionList(counter())
    c = a.count_matches(out("SRC", "Contents of URL"), "E1200")
    t, m = "U0", "U1"
    assert c == "U2"
    assert [x["WFWorkflowActionIdentifier"] for x in a] == [
        "is.workflow.actions.gettext",
        "is.workflow.actions.text.match",
        "is.workflow.actions.count",
    ]
    assert a[0]["WFWorkflowActionParameters"] == {"UUID": t, "WFTextActionText": ts(out("SRC", "Contents of URL"))}
    assert a[1]["WFWorkflowActionParameters"] == {
        "UUID": m,
        "WFMatchTextPattern": "E1200",
        "text": ts(out(t, "Text")),
    }
    assert a[2]["WFWorkflowActionParameters"] == {
        "UUID": c,
        "WFCountType": "Items",
        "WFInput": attach(out(m, "Matches")),
        "Input": attach(out(m, "Matches")),
    }


def test_count_matches_default_pattern_is_any_non_space():
    a = ActionList(counter())
    a.count_matches(var("School Code"))
    assert a[1]["WFWorkflowActionParameters"]["WFMatchTextPattern"] == r"\S"


def test_count_matches_without_coercion_matches_the_source_directly():
    """The publisher's `count_of()` shape: two actions, no Text."""
    a = ActionList(counter())
    c = a.count_matches(out("SRC", "Library Names"), "^X$", coerce=False)
    assert len(a) == 2
    assert a[0]["WFWorkflowActionParameters"]["text"] == ts(out("SRC", "Library Names"))
    assert c == "U1"
    assert a[0]["WFWorkflowActionParameters"]["UUID"] == "U0"


def test_if_markers():
    a = ActionList(counter())
    a.if_open("G", condition=GREATER_THAN, number=0, source=out("C", "Count"))
    a.if_else("G")
    a.if_close("G")
    assert a[0]["WFWorkflowActionParameters"] == {
        "UUID": "U0",
        "GroupingIdentifier": "G",
        "WFControlFlowMode": OPEN,
        "WFCondition": 2,
        "WFNumberValue": "0",
        "WFInput": cond_input(out("C", "Count")),
    }
    assert a[1]["WFWorkflowActionParameters"] == {"UUID": "U1", "GroupingIdentifier": "G", "WFControlFlowMode": ELSE}
    assert a[2]["WFWorkflowActionParameters"] == {"UUID": "U2", "GroupingIdentifier": "G", "WFControlFlowMode": CLOSE}
    assert LESS_THAN == 0


def test_repeat_and_menu_markers_are_balanced_and_pass_the_check():
    a = ActionList(counter())
    a.repeat_count_open("R", 2)
    a.repeat_each_open("E", out("L", "Students"))
    a.menu_open("M", "Which?", ["In", "Out"])
    a.menu_case("M", "In")
    a.menu_case("M", "Out")
    a.menu_close("M")
    a.repeat_each_close("E")
    a.repeat_count_close("R")
    assert a[0]["WFWorkflowActionParameters"]["WFRepeatCount"] == 2
    assert a[1]["WFWorkflowActionParameters"]["WFInput"] == attach(out("L", "Students"))
    assert a[2]["WFWorkflowActionParameters"]["WFMenuItems"] == ["In", "Out"]
    assert a[3]["WFWorkflowActionParameters"]["WFMenuItemTitle"] == "In"
    check_control_flow({"WFWorkflowActions": list(a)})


def test_notify_and_exit():
    a = ActionList(counter())
    a.notify(ts("T"), ts("B"))
    a.notify(body=ts("only body"))
    a.exit()
    assert a[0]["WFWorkflowActionParameters"] == {
        "WFNotificationActionTitle": ts("T"),
        "WFNotificationActionBody": ts("B"),
    }
    assert a[1]["WFWorkflowActionParameters"] == {"WFNotificationActionBody": ts("only body")}
    assert a[2] == {"WFWorkflowActionIdentifier": "is.workflow.actions.exit", "WFWorkflowActionParameters": {}}


def test_it_is_a_list():
    a = ActionList(counter())
    a.comment("c")
    a.append({"WFWorkflowActionIdentifier": "x", "WFWorkflowActionParameters": {}})
    assert len(a) == 2
    assert isinstance(a, list)
    with pytest.raises(StopIteration):
        next(ActionList(iter([])).uuids)
