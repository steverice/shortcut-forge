"""What a Shortcuts library holds, read from its database rather than its screen.

Every library — a Mac's, a simulator's, a macOS guest's — is the same Core
Data store at `~/Library/Shortcuts/Shortcuts.sqlite`, and it answers questions
the app hides: how many actions an installed copy has, whether its setup
questions survived the import, whether anyone answered them. Read a copy
taken with its `-wal` and `-shm` files, or `sqlite3 <db> ".backup <out>"`: the
main file alone is the library as it stood before the latest writes, and on a
guest measured 2026-09-18 it listed none of the three shortcuts just imported.

Moved from brightwheel-checkin's pre-mint gate, which is why everything here
fails closed. A blob that will not parse is `unreadable`, never zero actions
and zero questions, because zero of everything is what a *wrong* build looks
like. And a name with no build in `dist/` is left out of `expected_builds`
rather than guessed at, so the caller's policy has to refuse the gap. What
counts as a safe library is the caller's to decide; this only reads.
"""

from __future__ import annotations

import plistlib
import re
import sqlite3
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

#: What an *unanswered* import question carries, measured against a clean
#: import on 2026-09-15. An answer arrives as an extra key, and Apple's name for
#: it is not documented, so any key outside this set counts as the answer.
QUESTION_KEYS = frozenset({"ActionIndex", "Category", "DefaultValue", "ParameterKey", "Text"})

#: `Name 1`, which is what a second import of `Name` leaves behind on a Mac.
NUMBERED = re.compile(r"^(?P<base>.+?) (?P<n>\d+)$")


class LibraryError(RuntimeError):
    """A library database that could not be read as one."""


class UnreadableError(LibraryError):
    """A blob that is not a plist this module understands."""


@dataclass(frozen=True)
class Installed:
    """One shortcut as the library holds it."""

    name: str
    action_count: int = 0
    question_count: int = 0
    answered_questions: int = 0
    #: A blob that would not parse. It must never read as "zero of everything".
    unreadable: bool = False
    #: Deleted but not yet purged. Reported, never filtered: whether a
    #: tombstoned copy matters is the caller's policy.
    tombstoned: bool = False
    #: The raw question and action blobs, for a caller's own searches.
    blobs: tuple[bytes, ...] = field(default=(), repr=False, compare=False)

    def contains(self, pattern: re.Pattern[bytes]) -> bool:
        """Whether `pattern` matches anywhere in this copy's questions or actions."""
        return any(pattern.search(blob) for blob in self.blobs)


@dataclass(frozen=True)
class Expected:
    """What a built plist says a clean copy looks like."""

    actions: int
    questions: int


def numbered_base(name: str) -> str | None:
    """The name a numbered copy shadows (`"Name"` for `"Name 1"`), or None."""
    match = NUMBERED.match(name)
    return match.group("base") if match else None


def _load(blob: bytes) -> object:
    try:
        return plistlib.loads(blob)
    except (plistlib.InvalidFileException, ValueError, EOFError, TypeError) as exc:
        raise UnreadableError(str(exc)) from exc


def _count_actions(blob: bytes | None) -> int:
    if not blob:
        return 0
    parsed = _load(blob)
    if isinstance(parsed, dict):
        return len(parsed.get("WFWorkflowActions", []))
    return len(parsed) if isinstance(parsed, list) else 0


def _count_questions(blob: bytes | None) -> int:
    """Setup questions on the copy, answered or not.

    The only thing that can see a copy which lost its questions on import,
    since that leaves the action count untouched.
    """
    if not blob:
        return 0
    parsed = _load(blob)
    return len(parsed) if isinstance(parsed, list) else 0


def _count_answers(blob: bytes | None) -> int:
    if not blob:
        return 0
    parsed = _load(blob)
    if not isinstance(parsed, list):
        return 0
    return sum(1 for q in parsed if isinstance(q, dict) and any(v for k, v in q.items() if k not in QUESTION_KEYS))


def read_library(database: Path | str, prefix: str = "") -> list[Installed]:
    """Every shortcut whose name starts with `prefix`, as the database has it.

    Every row comes back, tombstoned ones included and flagged. The simulator
    harness filters `ZTOMBSTONED = 0` because it asks what a run will find; a
    caller deciding what is safe to publish must not have a row dropped before
    its policy sees it, which is the fail-open shape the pre-mint gate was
    fixed for. Tombstoned rows are rare in practice — measured 2026-09-17, a
    Replace duplicate is not tombstoned and a delete leaves no row at all.

    `prefix` goes to SQL `LIKE`, so it matches ASCII letters without regard to
    case, and a `%` or `_` in it is a wildcard.

    Opened read-only through a URI, so a running Shortcuts.app is neither
    disturbed nor able to disturb the read. A missing file, or one that is not
    a Shortcuts library, raises `LibraryError` rather than reading as an empty
    library.
    """
    try:
        con = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT ZSHORTCUT.ZNAME, ZSHORTCUT.ZTOMBSTONED, ZSHORTCUT.ZIMPORTQUESTIONSDATA, ZSHORTCUTACTIONS.ZDATA "
                "FROM ZSHORTCUT LEFT JOIN ZSHORTCUTACTIONS ON ZSHORTCUTACTIONS.Z_PK = ZSHORTCUT.ZACTIONS "
                "WHERE ZSHORTCUT.ZNAME LIKE ? ORDER BY ZSHORTCUT.ZNAME",
                (f"{prefix}%",),
            ).fetchall()
        finally:
            con.close()
    except sqlite3.DatabaseError as exc:
        raise LibraryError(f"could not read {database} as a Shortcuts library: {exc}") from exc

    found = []
    for name, tombstoned, questions, actions in rows:
        blobs = tuple(bytes(b) for b in (questions, actions) if b)
        try:
            found.append(
                Installed(
                    name=name,
                    action_count=_count_actions(actions),
                    question_count=_count_questions(questions),
                    answered_questions=_count_answers(questions),
                    tombstoned=bool(tombstoned),
                    blobs=blobs,
                )
            )
        except UnreadableError:
            found.append(Installed(name=name, unreadable=True, tombstoned=bool(tombstoned), blobs=blobs))
    return found


def expected_builds(dist: Path, names: list[str]) -> dict[str, Expected]:
    """What each `dist/<name>.xml` says a clean copy looks like, by name.

    A name whose plist is absent is skipped rather than guessed at; the caller
    refuses the gap. Nothing here may invent an expectation, because an
    invented one is indistinguishable from a met one.
    """
    built = {}
    for name in names:
        path = dist / f"{name}.xml"
        if not path.exists():
            continue
        raw = path.read_bytes()
        doc = _load(raw)
        questions = doc.get("WFWorkflowImportQuestions", []) if isinstance(doc, dict) else []
        built[name] = Expected(actions=_count_actions(raw), questions=len(questions))
    return built
