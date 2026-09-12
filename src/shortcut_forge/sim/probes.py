"""Tiny shortcuts that test one platform mechanism in isolation.

When a whole flow breaks, a two-action shortcut says whether the platform is
the problem without any of a project's own machinery being involved.
"""

from __future__ import annotations

from typing import Any

from shortcut_forge.plist import act, attach, document, import_question, out
from shortcut_forge.uuids import random_uuids

SETUP_PROBE_PLACEHOLDER = "not set"
"""What the probe's Text action holds until an import question overwrites it."""


def setup_probe(name: str, *, question: str = "Setup canary — type the digits shown by the test") -> dict[str, Any]:
    """A two-action shortcut whose only value comes from an import question.

    Text holds a placeholder; the import question replaces it; Copy to
    Clipboard makes the value observable. Installed and read back off the
    device, it answers "does answering a setup question actually configure
    the shortcut?", which regressed during the iOS 27 beta cycle and is worth
    watching on every runtime. The question asks for digits because the
    answer field autocapitalizes letters.
    """
    u = next(random_uuids())
    actions = [
        act("is.workflow.actions.gettext", UUID=u, CustomOutputName="Answer", WFTextActionText=SETUP_PROBE_PLACEHOLDER),
        act("is.workflow.actions.setclipboard", WFInput=attach(out(u, "Answer"))),
    ]
    return document(
        name,
        actions,
        glyph=59692,
        color=4292093695,
        questions=[import_question(0, "WFTextActionText", question)],
        input_classes=[],
    )
