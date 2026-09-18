"""Reading a Shortcuts library database: every way a copy can be misread fails closed.

Moved from brightwheel-checkin's pre-mint gate with the reader. The fixture is
the subset of the Core Data schema the reader touches.
"""

from __future__ import annotations

import plistlib
import re
import sqlite3
from typing import TYPE_CHECKING

import pytest

from shortcut_forge_lib.library import Expected, LibraryError, expected_builds, numbered_base, read_library

if TYPE_CHECKING:
    from pathlib import Path


def fixture_db(
    path: Path, rows: list[tuple[str, bytes | None, bytes | None]], *, tombstoned: set[str] = frozenset()
) -> Path:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE ZSHORTCUTACTIONS (Z_PK INTEGER PRIMARY KEY, ZDATA BLOB)")
    con.execute(
        "CREATE TABLE ZSHORTCUT (Z_PK INTEGER PRIMARY KEY, ZNAME TEXT, ZTOMBSTONED INTEGER, "
        "ZIMPORTQUESTIONSDATA BLOB, ZACTIONS INTEGER)"
    )
    for index, (name, questions, actions) in enumerate(rows, start=1):
        con.execute("INSERT INTO ZSHORTCUTACTIONS (Z_PK, ZDATA) VALUES (?, ?)", (index, actions))
        con.execute(
            "INSERT INTO ZSHORTCUT (Z_PK, ZNAME, ZTOMBSTONED, ZIMPORTQUESTIONSDATA, ZACTIONS) VALUES (?, ?, ?, ?, ?)",
            (index, name, int(name in tombstoned), questions, index),
        )
    con.commit()
    con.close()
    return path


def actions_blob(count: int, text: str = "not set") -> bytes:
    action = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.gettext",
        "WFWorkflowActionParameters": {"WFTextActionText": text},
    }
    return plistlib.dumps({"WFWorkflowActions": [action] * count})


def questions_blob(count: int, *, answered: bool = False) -> bytes:
    q: dict[str, object] = {"ParameterKey": "WFTextActionText", "Text": "Your email", "DefaultValue": "not set"}
    if answered:
        q["ActualValue"] = "someone@example.invalid"
    return plistlib.dumps([q] * count)


def test_counts_actions_questions_and_answers(tmp_path):
    db = fixture_db(tmp_path / "Shortcuts.sqlite", [("Target", questions_blob(3), actions_blob(339))])

    found = read_library(db, prefix="Target")[0]

    assert (found.action_count, found.question_count, found.answered_questions) == (339, 3, 0)
    assert found.unreadable is False
    assert found.tombstoned is False


def test_an_answer_is_any_key_outside_the_unanswered_set(tmp_path):
    db = fixture_db(tmp_path / "Shortcuts.sqlite", [("Target", questions_blob(3, answered=True), actions_blob(10))])

    assert read_library(db)[0].answered_questions == 3


def test_contains_searches_questions_and_actions(tmp_path):
    email = re.compile(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
    db = fixture_db(
        tmp_path / "Shortcuts.sqlite",
        [
            ("Answered", questions_blob(1, answered=True), actions_blob(2)),
            ("Baked", None, actions_blob(2, text="parent@example.invalid")),
            ("Clean", questions_blob(1), actions_blob(2)),
        ],
    )

    hits = {s.name: s.contains(email) for s in read_library(db)}

    assert hits == {"Answered": True, "Baked": True, "Clean": False}


def test_an_unparseable_blob_is_unreadable_not_zero(tmp_path):
    db = fixture_db(tmp_path / "Shortcuts.sqlite", [("Target", None, b"this is not a plist")])

    found = read_library(db)[0]

    assert found.unreadable is True


def test_a_tombstoned_copy_is_reported_not_dropped(tmp_path):
    db = fixture_db(
        tmp_path / "Shortcuts.sqlite",
        [("Target", None, actions_blob(5)), ("Target", None, actions_blob(4))],
        tombstoned={"Target"},
    )

    found = read_library(db)

    assert len(found) == 2
    assert all(s.tombstoned for s in found)


def test_the_prefix_limits_what_is_read(tmp_path):
    db = fixture_db(
        tmp_path / "Shortcuts.sqlite", [("Keep me", None, actions_blob(1)), ("Other", None, actions_blob(1))]
    )

    assert [s.name for s in read_library(db, prefix="Keep")] == ["Keep me"]


def test_a_missing_database_raises_rather_than_reading_empty(tmp_path):
    with pytest.raises(LibraryError):
        read_library(tmp_path / "nowhere.sqlite")


def test_a_database_that_is_not_a_shortcuts_library_raises(tmp_path):
    other = tmp_path / "other.sqlite"
    sqlite3.connect(other).execute("CREATE TABLE unrelated (x)").connection.close()

    with pytest.raises(LibraryError, match="Shortcuts library"):
        read_library(other)


def test_expected_builds_reads_actions_and_questions(tmp_path):
    doc = plistlib.loads(actions_blob(17))
    doc["WFWorkflowImportQuestions"] = [{"ParameterKey": "WFTextActionText"}] * 3
    (tmp_path / "Target.xml").write_bytes(plistlib.dumps(doc))

    assert expected_builds(tmp_path, ["Target"]) == {"Target": Expected(actions=17, questions=3)}


def test_expected_builds_skips_a_name_with_no_plist(tmp_path):
    assert expected_builds(tmp_path, ["Target"]) == {}


@pytest.mark.parametrize(
    ("name", "base"),
    [
        ("Brightwheel Attendance 1", "Brightwheel Attendance"),
        ("Target 12", "Target"),
        ("Target", None),
        ("Target1", None),
    ],
)
def test_numbered_base(name, base):
    assert numbered_base(name) == base
