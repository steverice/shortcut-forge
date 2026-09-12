"""Check that an iCloud link actually delivers the shortcut that was built.

A link is a snapshot of whatever was in the sharing device's library, and it
can come out missing its setup questions. The failure is silent: the shortcut
installs in one tap, looks right, and leaves the placeholder in every
credential action, so the first sign it went wrong is a user whose install
never works. So every link gets imported on a simulator and compared against
the build it is supposed to be carrying, before it goes anywhere near a page.
"""

from __future__ import annotations

import plistlib
import sqlite3
import subprocess
import time
from typing import TYPE_CHECKING

from shortcut_forge.plist import read_xml

if TYPE_CHECKING:
    from pathlib import Path

    from shortcut_forge.sim.harness import Simulator


class LinkError(RuntimeError):
    """The link could not be opened or imported at all."""


def question_count(sim: Simulator, name: str) -> int:
    """Import questions on the installed copy, straight out of its database."""
    con = sqlite3.connect(f"file:{sim.db_path}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT ZIMPORTQUESTIONSDATA FROM ZSHORTCUT WHERE ZNAME = ? AND ZTOMBSTONED = 0", (name,)
        ).fetchone()
    finally:
        con.close()
    if not row or not row[0]:
        return 0
    return len(plistlib.loads(bytes(row[0])))


def install_from_link(sim: Simulator, link: str, *, timeout: float = 45, settle: float = 6) -> None:
    """Open a link and take whichever import path the sheet offers.

    A sheet with questions offers Set Up Shortcut and then needs Skip Setup,
    which is the only way to finish that keeps the questions. One without
    questions installs on the first tap. Both are handled, because which one
    appears is the thing being measured.

    The link is opened a second time before giving up. On a freshly erased
    simulator the first open left the home screen showing, and the same link
    opened normally on the next try, so one failure alone says nothing about
    the link.
    """
    for attempt in (1, 2):
        sim.terminate_shortcuts()
        time.sleep(1.2)
        subprocess.run(["xcrun", "simctl", "openurl", sim.udid, link], check=True)
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(2)
            if sim.blue_buttons():
                break
        else:
            if attempt == 1:
                continue
            raise LinkError("no import sheet appeared on either try — is the link still live?")
        break
    sim.tap_affirmative()
    time.sleep(4)
    boxes = sim.blue_buttons()
    if boxes:  # the questions page: Skip Setup sits below the blue button
        img = sim.image()
        w, h = img.size
        _, _, _, y1 = max(boxes, key=lambda b: (b[3], b[2]))
        sim.tap(w // 2, int(y1 + h * 0.045), device_size=(w, h))
        time.sleep(settle)


def check_link(sim: Simulator, name: str, link: str, xml: Path) -> list[str]:
    """Import `link` and compare the installed shortcut against the built `xml`.

    Returns the problems found, or an empty list. Checks: the shortcut
    installed under `name`, with the same action identifiers in the same
    order as the build, and the same number of import questions.
    """
    if not xml.exists():
        return [f"no {xml} to compare against — build first"]
    want = read_xml(xml)
    if name in sim.library():
        return [f"{name!r} is already installed, so the import would be silently skipped. Erase the library first."]

    install_from_link(sim, link)

    problems = []
    if name not in sim.library():
        return [f"{name!r} did not install"]
    got = sim.shortcut_actions(name) or []
    want_ids = [a["WFWorkflowActionIdentifier"] for a in want["WFWorkflowActions"]]
    got_ids = [a["WFWorkflowActionIdentifier"] for a in got]
    if got_ids != want_ids:
        problems.append(f"actions differ: {len(got_ids)} installed, {len(want_ids)} built")
    want_q = len(want["WFWorkflowImportQuestions"])
    got_q = question_count(sim, name)
    if got_q != want_q:
        problems.append(
            f"import questions: {got_q} on the link, {want_q} built. "
            "Anyone installing this is never asked to set it up."
        )
    return problems
