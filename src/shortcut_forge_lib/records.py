"""Checking a minted iCloud link without a device: the records API serves the unsigned plist.

A link is a snapshot of whatever the sharing library held, so before one goes
anywhere public it is compared against the build it is supposed to carry.
`https://www.icloud.com/shortcuts/api/records/<id>` answers with the record's
name and a `downloadURL` for the unsigned plist that was shared, so the check
needs no simulator, no import, and no Shortcuts window: the record name, the
action identifiers in order, and every import question's `ActionIndex`,
`ParameterKey` and `Category` against the built XML.

Measured 2026-09-18 on six real links: the three v1.4.0 links a page was still
serving passed against the v1.4.0 build and failed against v1.5.0 at action 89,
and the three v1.5.0 links minted in a guest passed against v1.5.0 and failed
the other way. It is a stronger check than a simulator import for the failure
that has actually occurred — a stale or wrong build behind a link — and the
simulator path cannot run under Xcode 27 at all.

Only `urllib` is used, and every fetch goes through an injectable callable, so
the tests make no network call.
"""

from __future__ import annotations

import json
import plistlib
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from shortcut_forge_lib.library import QUESTION_KEYS

if TYPE_CHECKING:
    from pathlib import Path

PREFIX = "https://www.icloud.com/shortcuts/"
RECORDS = PREFIX + "api/records/"
_ID = re.compile(r"[0-9a-f]{32}")

#: Takes a URL, returns the body. The default is `urllib`; tests pass a dict lookup.
Fetch = Callable[[str], bytes]


class RecordError(RuntimeError):
    """The link's record or its plist could not be fetched or read."""


def _urlopen(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - https URLs built above
            return response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RecordError(f"could not fetch {url}: {exc}") from exc


@dataclass(frozen=True)
class Record:
    """What the records API says a link carries."""

    link: str
    name: str
    plist: dict[str, Any]
    created: datetime | None
    signing_status: str | None


def record_id(link: str) -> str:
    """The 32-hex-digit id at the end of an iCloud shortcut link."""
    if not link.startswith(PREFIX):
        raise RecordError(f"{link!r} is not an iCloud shortcut link; those start with {PREFIX}")
    rid = link.removeprefix(PREFIX).strip("/")
    if not _ID.fullmatch(rid):
        raise RecordError(f"{link!r} does not end in a record id")
    return rid


def fetch_record(link: str, *, fetch: Fetch = _urlopen) -> Record:
    """The record behind `link`, with its unsigned plist downloaded and parsed."""
    try:
        record = json.loads(fetch(RECORDS + record_id(link)))
        fields = record["fields"]
        # The download URL carries a `${f}` placeholder for a file name; any name works.
        url = fields["shortcut"]["value"]["downloadURL"].replace("${f}", "shortcut.plist")
        name = fields["name"]["value"]
    except (ValueError, KeyError, TypeError) as exc:
        raise RecordError(f"the record for {link} is not in the expected shape: {exc!r}") from exc
    try:
        plist = plistlib.loads(fetch(url))
    except (plistlib.InvalidFileException, ValueError) as exc:
        raise RecordError(f"the plist behind {link} did not parse: {exc}") from exc
    created = record.get("created", {}).get("timestamp")
    return Record(
        link=link,
        name=name,
        plist=plist,
        created=datetime.fromtimestamp(created / 1000, timezone.utc) if created else None,
        signing_status=fields.get("signingStatus", {}).get("value"),
    )


def _questions(doc: dict[str, Any]) -> list[tuple[Any, Any, Any]]:
    return [
        (q.get("ActionIndex"), q.get("ParameterKey"), q.get("Category"))
        for q in doc.get("WFWorkflowImportQuestions", [])
    ]


def compare(record: Record, name: str, built: dict[str, Any]) -> list[str]:
    """Every way `record` differs from the `built` plist it should carry, or nothing."""
    problems = []
    if record.name != name:
        problems.append(f"the record is named {record.name!r}, not {name!r}")
    got = [a["WFWorkflowActionIdentifier"] for a in record.plist.get("WFWorkflowActions", [])]
    want = [a["WFWorkflowActionIdentifier"] for a in built.get("WFWorkflowActions", [])]
    if got != want:
        first = next((i for i, (x, y) in enumerate(zip(got, want, strict=False)) if x != y), min(len(got), len(want)))
        problems.append(f"actions: {len(got)} on the link, {len(want)} built, first difference at index {first}")
    if _questions(record.plist) != _questions(built):
        problems.append(f"import questions: {_questions(record.plist)} on the link, {_questions(built)} built")
    answered = [
        q
        for q in record.plist.get("WFWorkflowImportQuestions", [])
        if any(v for k, v in q.items() if k not in QUESTION_KEYS)
    ]
    if answered:
        problems.append(
            f"{len(answered)} import question(s) on the link carry answers, so it was shared from a configured copy"
        )
    return problems


def check_record(link: str, name: str, xml: Path, *, fetch: Fetch = _urlopen) -> list[str]:
    """Fetch `link`'s record and compare it against the built `xml`. Empty means it carries that build."""
    if not xml.exists():
        return [f"no {xml} to compare against — build first"]
    with xml.open("rb") as handle:
        built = plistlib.load(handle)
    return compare(fetch_record(link, fetch=fetch), name, built)
