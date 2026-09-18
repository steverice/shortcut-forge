"""The share-links shortcut: nothing stored that can go stale, nothing minted until every check passes.

Moved from brightwheel-checkin, where the three targets were fixed. The
baseline fixture is that project's real build, regenerated on 2026-09-18 when
the closing Show Result became a notification, and the structure here must
match it action for action.
"""

from __future__ import annotations

import json
import re

import pytest

from shortcut_forge_lib.publisher import (
    DEFAULT_LINE_FORMAT,
    ICLOUD_LINK_ACTION,
    links_from_markup,
    share_links_shortcut,
)

TARGETS = ["Brightwheel Attendance", "Brightwheel Check In", "Brightwheel Check Out"]
REPEAT = "is.workflow.actions.repeat.each"
EXIT = "is.workflow.actions.exit"


def actions():
    return share_links_shortcut("Brightwheel Share Links", TARGETS)["WFWorkflowActions"]


def ident(a):
    return a["WFWorkflowActionIdentifier"]


def params(a):
    return a["WFWorkflowActionParameters"]


def positions(acts, identifier):
    return [n for n, a in enumerate(acts) if ident(a) == identifier]


def test_action_sequence_matches_the_baseline_build(publisher_baseline):
    """Same actions in the same order as the build brightwheel-checkin shipped."""
    assert [ident(a) for a in actions()] == [ident(a) for a in publisher_baseline["WFWorkflowActions"]]


def test_non_comment_parameters_match_the_baseline_build(publisher_baseline):
    """Everything but UUIDs and comment prose is identical to the baseline."""

    def strip(acts):
        out = []
        for a in acts:
            if ident(a) == "is.workflow.actions.comment":
                continue
            text = re.sub(
                r"[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}",
                "UUID",
                json.dumps(params(a), sort_keys=True),
            )
            out.append((ident(a), text))
        return out

    ours, theirs = strip(actions()), strip(publisher_baseline["WFWorkflowActions"])
    # The closing notification's body is the consumer's `done_message`.
    assert ours[-1][0] == theirs[-1][0] == "is.workflow.actions.notification"
    assert ours[:-1] == theirs[:-1]


def test_target_names_need_no_regex_escaping():
    with pytest.raises(ValueError, match="regex escaping"):
        share_links_shortcut("P", ["A.B"])


def test_every_link_is_found_at_run_time_not_picked():
    links = [a for a in actions() if ident(a) == ICLOUD_LINK_ACTION]
    assert len(links) == 3
    for a in links:
        assert params(a)["shortcut"] == {
            "Value": {"Type": "Variable", "VariableName": "Repeat Item"},
            "WFSerializationType": "WFTextTokenAttachment",
        }
        assert params(a)["AppIntentDescriptor"]["BundleIdentifier"] == "com.apple.shortcuts"


def test_links_are_minted_inside_the_single_library_walk():
    acts = actions()
    opens = [n for n in positions(acts, REPEAT) if params(acts[n])["WFControlFlowMode"] == 0]
    closes = [n for n in positions(acts, REPEAT) if params(acts[n])["WFControlFlowMode"] == 2]
    assert len(opens) == 1
    assert len(closes) == 1
    assert all(opens[0] < n < closes[0] for n in positions(acts, ICLOUD_LINK_ACTION))


def test_nothing_is_minted_until_every_check_has_passed():
    acts = actions()
    exits = positions(acts, EXIT)
    assert len(exits) == 3 * len(TARGETS)  # more-than-one, numbered, missing
    assert max(exits) < min(positions(acts, ICLOUD_LINK_ACTION))


def test_each_target_is_checked_and_matched_by_name():
    pats = [params(a)["WFMatchTextPattern"] for a in actions() if ident(a) == "is.workflow.actions.text.match"]
    for t in TARGETS:
        assert f"(?m)^{t}( \\d+)?$" in pats  # more than one copy
        assert f"(?m)^{t} \\d+$" in pats  # the only copy is numbered
        assert f"(?m)^{t}$" in pats  # missing
        assert f"^{t}$" in pats  # the walk: exact name only


def test_patterns_mean_what_the_checks_claim():
    for t in TARGETS:
        lib = f"Other\n{t}\n{t} 1\nBrightwheel Share Links"
        assert len(re.findall(f"(?m)^{t}( \\d+)?$", lib)) == 2
        assert len(re.findall(f"(?m)^{t} \\d+$", lib)) == 1
        assert len(re.findall(f"(?m)^{t}$", lib)) == 1
        assert re.search(f"^{t}$", t)
        assert not re.search(f"^{t}$", f"{t} 1")
        same_name = f"Other\n{t}\n{t}\nBrightwheel Share Links"
        assert len(re.findall(f"(?m)^{t}( \\d+)?$", same_name)) == 2


def test_more_than_one_explains_the_copy_you_cannot_see():
    notes = {
        params(a)["WFNotificationActionTitle"]["Value"]["string"]: params(a)["WFNotificationActionBody"]["Value"][
            "string"
        ]
        for a in actions()
        # The refusals carry a title; the closing notification is a body alone.
        if ident(a) == "is.workflow.actions.notification" and "WFNotificationActionTitle" in params(a)
    }
    for t in TARGETS:
        assert "Replace" in notes[f"More than one {t}"]


def test_clipboard_gets_the_rendered_lines_in_order():
    acts = share_links_shortcut("P", TARGETS, line_format='<li><a href="{link}">{name}</a></li>\n')["WFWorkflowActions"]
    markup = [a for a in acts if params(a).get("CustomOutputName") == "Markup"]
    assert len(markup) == 1
    token = params(markup[0])["WFTextActionText"]["Value"]
    for t in TARGETS:
        assert f'">{t}</a></li>' in token["string"]
    used = [
        v.get("VariableName")
        for _, v in sorted(token["attachmentsByRange"].items(), key=lambda kv: int(kv[0][1:].split(",")[0]))
    ]
    assert used == [f"{t} Link" for t in TARGETS]
    assert len(positions(acts, "is.workflow.actions.setclipboard")) == 1


def test_done_message_is_a_notification_not_a_sheet():
    # Show Result holds `shortcuts run` open behind a Done sheet on every run.
    acts = share_links_shortcut("P", ["A"], done_message="Now verify.")["WFWorkflowActions"]
    assert ident(acts[-1]) == "is.workflow.actions.notification"
    assert params(acts[-1])["WFNotificationActionBody"]["Value"]["string"] == "Now verify."
    assert not positions(acts, "is.workflow.actions.showresult")


def rendered(links: dict[str, str], line_format: str = DEFAULT_LINE_FORMAT) -> str:
    """What the shortcut puts on the clipboard, rendered the way its Markup text does."""
    return "".join(line_format.format(name=name, link=link) for name, link in links.items())


LINKS = {t: f"https://www.icloud.com/shortcuts/{n:032x}" for n, t in enumerate(TARGETS, start=1)}


def test_links_read_back_from_what_was_rendered():
    assert links_from_markup(rendered(LINKS), TARGETS) == LINKS


def test_links_are_read_by_name_not_position():
    reordered = dict(reversed(list(LINKS.items())))

    assert links_from_markup(rendered(reordered), TARGETS) == LINKS


def test_a_missing_line_leaves_that_name_out():
    partial = {t: LINKS[t] for t in TARGETS[:2]}

    assert links_from_markup(rendered(partial), TARGETS) == partial


def test_a_dropped_trailing_newline_still_reads():
    assert links_from_markup(rendered(LINKS).rstrip(), TARGETS) == LINKS


def test_a_custom_line_format_reads_back_through_the_same_format():
    fmt = "{name}: {link}\n"

    assert links_from_markup(rendered(LINKS, fmt), TARGETS, fmt) == LINKS


def test_a_name_that_only_prefixes_another_is_not_matched():
    # "Brightwheel Check" must not read Check In's line.
    assert links_from_markup(rendered(LINKS), ["Brightwheel Check"]) == {}


def test_it_asks_no_setup_questions():
    doc = share_links_shortcut("P", TARGETS)
    assert doc["WFWorkflowImportQuestions"] == []
    assert doc["WFWorkflowName"] == "P"
    assert doc["WFWorkflowIcon"] == {"WFWorkflowIconGlyphNumber": 59749, "WFWorkflowIconStartColor": 946986751}
