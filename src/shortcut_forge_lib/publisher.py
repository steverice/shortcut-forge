"""A shortcut that mints iCloud share links for other shortcuts, by name.

An iCloud link is a snapshot taken when you share, so every release needs
fresh ones. The `shortcuts` CLI has no share command and the AppleScript
dictionary exposes only `run`, so the minting has to happen inside a
shortcut, and `run` is all it takes to drive one:

    shortcuts run "<name>"

It exits 0 with every link on the clipboard, rendered through `line_format`.

The shortcut finds each target by name every time it runs rather than through
a picker. A picker stores a workflow identifier, publishing a new build mints
a new one, and a picked target therefore goes stale on every release. Looking
the shortcut up stores nothing that can go stale.

Before minting anything it checks that the library holds exactly one copy of
each target under its exact name, and stops with a notification otherwise. A
numbered copy ("Attendance 1", which macOS leaves when you import over an
existing name) is refused rather than shared, because people would install
it under that name.
"""

from __future__ import annotations

from typing import Any

from shortcut_forge_lib.actions import GREATER_THAN, ActionList
from shortcut_forge_lib.plist import attach, document, out, ts, var
from shortcut_forge_lib.uuids import random_uuids

ICLOUD_LINK_ACTION = "com.apple.shortcuts.CreateShortcutiCloudLinkAction"
"""Verified against the v78 ToolKit database: "Create iCloud Link for Shortcut", one parameter keyed `shortcut`."""

DEFAULT_LINE_FORMAT = '<li><a href="{link}">{name}</a></li>\n'


def app_intent_descriptor() -> dict[str, str]:
    """Every `com.apple.*` AppIntent needs one, or the file will not import."""
    return {
        "BundleIdentifier": "com.apple.shortcuts",
        "Name": "Shortcuts",
        "TeamIdentifier": "0000000000",
        "AppIntentIdentifier": "CreateShortcutiCloudLinkAction",
    }


def share_links_shortcut(
    name: str,
    targets: list[str],
    *,
    line_format: str = DEFAULT_LINE_FORMAT,
    done_message: str = "Copied the links.",
    glyph: int = 59749,
    color: int = 946986751,
) -> dict[str, Any]:
    """Build the publisher for `targets`.

    `line_format` renders each link; `{name}` and `{link}` are the two fields,
    and the results are concatenated onto the clipboard in `targets` order.
    Target names must be letters and spaces only: they are interpolated into
    match patterns raw.
    """
    for target in targets:
        if not all(c.isalpha() or c == " " for c in target):
            raise ValueError(f"target name {target!r} needs regex escaping; only letters and spaces are supported")

    a = ActionList(random_uuids())
    a.comment(
        "--- WHAT THIS IS ---\n"
        f"Mints a fresh iCloud link for each of {len(targets)} shortcuts and copies them to "
        "the clipboard.\n\n"
        "It finds each one by name every time it runs, so a fresh import is simply "
        "found again. Nothing needs picking, and nothing goes stale between "
        "releases.\n\n"
        "Each link asks you to confirm. If anything about the library is wrong it "
        "stops before the first one."
    )
    u_all = a.uuid()
    a.add("is.workflow.actions.getmyworkflows", UUID=u_all, CustomOutputName="My Shortcuts")
    # A list of shortcuts coerced to text is their names, one per line.
    u_names = a.text(ts(out(u_all, "My Shortcuts")), name="Library Names")
    u_one = a.number(1)

    def stop_if_above(src: str, output: str, value: str, why: str, title: str, body: str) -> None:
        group = a.uuid()
        a.comment(why)
        a.if_open(group, condition=GREATER_THAN, number=value, source=out(src, output))
        a.notify(ts(title), ts(body))
        a.exit()
        a.if_close(group)

    names = out(u_names, "Library Names")
    for t in targets:
        any_copy = a.count_matches(names, f"(?m)^{t}( \\d+)?$", coerce=False)
        stop_if_above(
            any_copy,
            "Count",
            "1",
            f"Stop if there is more than one {t}.\n"
            f"- Condition counts every copy, numbered or not\n"
            f"- Linking either one would be a guess about which is the new build\n"
            f"- Replace on the Mac hides the old copy from the app, not from this count",
            f"More than one {t}",
            "Delete every copy and import the new build once. If only one shows, "
            "delete it anyway: Replace hides the old copy until the new one is "
            "gone. Nothing was shared.",
        )
        numbered = a.count_matches(names, f"(?m)^{t} \\d+$", coerce=False)
        stop_if_above(
            numbered,
            "Count",
            "0",
            f"Stop if the only copy has a number after its name.\n"
            f"- Condition counts copies named {t} followed by a number\n"
            f"- It would be shared under that name, and anything calling {t} calls it by its exact name",
            f"{t} has a number after its name",
            f"Rename it to {t} and run this again. Nothing was shared.",
        )
        exact = a.count_matches(names, f"(?m)^{t}$", coerce=False)
        u_gap = a.uuid()
        a.add(
            "is.workflow.actions.math",
            UUID=u_gap,
            WFInput=attach(out(u_one, "Number")),
            WFMathOperation="-",
            WFMathOperand=attach(out(exact, "Count")),
        )
        stop_if_above(
            u_gap,
            "Calculation Result",
            "0",
            f"Stop if {t} is not in the library.\n"
            f"- Condition is one minus the exact-name count\n"
            f"- Above zero means there is none",
            f"{t} is not in your library",
            "Import it and run this again. Nothing was shared.",
        )

    g_loop = a.uuid()
    a.comment(
        f"Walk the library and link each of the {len(targets)}.\n"
        "- Input is My Shortcuts, the whole library\n"
        "- Each Repeat Item is one shortcut, found by its exact name"
    )
    a.repeat_each_open(g_loop, out(u_all, "My Shortcuts"))
    u_name = a.text(ts(var("Repeat Item")), name="Name")
    for t in targets:
        hit = a.count_matches(out(u_name, "Name"), f"^{t}$", coerce=False)
        g, u_link = a.uuid(), a.uuid()
        a.comment(
            f"Link {t} when this item is it.\n"
            f"- Condition counts an exact match on this item's name\n"
            f"- The item goes to Create iCloud Link as a variable, never a picked value"
        )
        a.if_open(g, condition=GREATER_THAN, number="0", source=out(hit, "Count"))
        a.add(
            ICLOUD_LINK_ACTION,
            UUID=u_link,
            CustomOutputName=f"Link for {t}",
            AppIntentDescriptor=app_intent_descriptor(),
            shortcut=attach(var("Repeat Item")),
        )
        a.add(
            "is.workflow.actions.setvariable", WFVariableName=f"{t} Link", WFInput=attach(out(u_link, f"Link for {t}"))
        )
        a.if_close(g)
    a.repeat_each_close(g_loop)

    parts: list[str | dict[str, Any]] = []
    for t in targets:
        before, _, after = line_format.partition("{link}")
        parts += [before.format(name=t), var(f"{t} Link"), after.format(name=t)]
    a.comment(
        "Copy the rendered links.\n"
        "- One line per shortcut, in order\n"
        "- Then verify the links before publishing them anywhere"
    )
    u_mk = a.text(ts(*parts), name="Markup")
    a.add("is.workflow.actions.setclipboard", WFInput=attach(out(u_mk, "Markup")))
    a.add("is.workflow.actions.showresult", Text=ts(done_message))

    return document(name, a, glyph=glyph, color=color, input_classes=[])
