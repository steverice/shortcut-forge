"""The records-API link check: every difference between a link and its build is reported, offline."""

from __future__ import annotations

import json
import plistlib
from typing import TYPE_CHECKING, Any

import pytest

from shortcut_forge_lib.records import RECORDS, RecordError, check_record, fetch_record, record_id

if TYPE_CHECKING:
    from pathlib import Path

RID = "2cef3907742c4386a9a152efa97945d2"
LINK = f"https://www.icloud.com/shortcuts/{RID}"
DOWNLOAD = "https://cvws.icloud-content.com/B/opaque/${f}?o=token"


def shortcut(ids: list[str], questions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "WFWorkflowActions": [{"WFWorkflowActionIdentifier": i, "WFWorkflowActionParameters": {}} for i in ids],
        "WFWorkflowImportQuestions": questions or [],
    }


QUESTION = {
    "ActionIndex": 12,
    "Category": "Parameter",
    "DefaultValue": "not set",
    "ParameterKey": "WFTextActionText",
    "Text": "Email",
}
BUILT = shortcut(
    ["is.workflow.actions.comment", "is.workflow.actions.gettext", "is.workflow.actions.notification"], [QUESTION]
)


def fake(name: str, plist: dict[str, Any]) -> dict[str, bytes]:
    record = {
        "fields": {
            "name": {"value": name},
            "shortcut": {"value": {"downloadURL": DOWNLOAD}},
            "signingStatus": {"value": "APPROVED"},
        },
        "created": {"timestamp": 1789769220179},
    }
    return {
        RECORDS + RID: json.dumps(record).encode(),
        DOWNLOAD.replace("${f}", "shortcut.plist"): plistlib.dumps(plist),
    }


def check(tmp_path: Path, name: str, served: dict[str, Any], built: dict[str, Any] = BUILT) -> list[str]:
    xml = tmp_path / f"{name}.xml"
    xml.write_bytes(plistlib.dumps(built))
    return check_record(LINK, name, xml, fetch=fake(name, served).__getitem__)


def test_a_link_carrying_the_build_passes(tmp_path):
    assert check(tmp_path, "Target", BUILT) == []


def test_the_record_is_read_whole():
    record = fetch_record(LINK, fetch=fake("Target", BUILT).__getitem__)

    assert (record.name, record.signing_status) == ("Target", "APPROVED")
    assert record.created is not None
    assert record.created.year == 2026
    assert len(record.plist["WFWorkflowActions"]) == 3


def test_a_stale_build_names_where_the_actions_part(tmp_path):
    stale = shortcut(["is.workflow.actions.comment", "is.workflow.actions.exit"], [QUESTION])

    problems = check(tmp_path, "Target", stale)

    assert problems == ["actions: 2 on the link, 3 built, first difference at index 1"]


def test_a_lost_question_is_reported(tmp_path):
    lost = shortcut([a["WFWorkflowActionIdentifier"] for a in BUILT["WFWorkflowActions"]])

    problems = check(tmp_path, "Target", lost)

    assert len(problems) == 1
    assert problems[0].startswith("import questions:")


def test_an_answered_question_on_the_link_is_refused(tmp_path):
    answered = dict(QUESTION, ActualValue="parent@example.invalid")
    served = shortcut([a["WFWorkflowActionIdentifier"] for a in BUILT["WFWorkflowActions"]], [answered])

    problems = check(tmp_path, "Target", served)

    assert problems == ["1 import question(s) on the link carry answers, so it was shared from a configured copy"]


def test_the_record_must_carry_the_expected_name(tmp_path):
    xml = tmp_path / "Target.xml"
    xml.write_bytes(plistlib.dumps(BUILT))

    problems = check_record(LINK, "Target", xml, fetch=fake("Target 1", BUILT).__getitem__)

    assert problems == ["the record is named 'Target 1', not 'Target'"]


def test_a_missing_build_is_a_problem_not_a_pass(tmp_path):
    problems = check_record(LINK, "Target", tmp_path / "Target.xml", fetch=fake("Target", BUILT).__getitem__)

    assert len(problems) == 1
    assert "build first" in problems[0]


@pytest.mark.parametrize(
    "link",
    ["https://example.com/shortcuts/" + RID, "https://www.icloud.com/shortcuts/not-an-id", ""],
)
def test_only_an_icloud_shortcut_link_is_accepted(link):
    with pytest.raises(RecordError):
        record_id(link)


def test_a_record_in_another_shape_raises():
    served = {RECORDS + RID: b'{"reason": "not found"}'}

    with pytest.raises(RecordError):
        fetch_record(LINK, fetch=served.__getitem__)
