"""Structural checks that run on a built document, before the validator does.

The validator that ships with the shortcuts-playground plugin checks a file
against its catalog. These check the things that pass that validator and then
fail on a device without a word: an attachment offset that no longer lands on
a placeholder, a reference to an action that was reordered away, a variable
reading a dictionary key nothing defines, and a control-flow block whose
markers do not pair. Each was discovered the expensive way in one project or
another, and the docstring of each check says how.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from shortcut_forge_lib.actions import CLOSE, CONDITIONAL, ELSE, MENU, OPEN
from shortcut_forge_lib.plist import OBJ

if TYPE_CHECKING:
    from collections.abc import Collection, Iterator


class CheckError(Exception):
    """A structural problem that would ship silently."""


def _actions(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return doc.get("WFWorkflowActions", [])


def _params(action: dict[str, Any]) -> dict[str, Any]:
    return action.get("WFWorkflowActionParameters", {})


def _walk(node: Any) -> Iterator[dict[str, Any]]:
    """Every dictionary anywhere inside `node`, depth first."""
    if isinstance(node, dict):
        yield node
        for child in node.values():
            yield from _walk(child)
    elif isinstance(node, list):
        for child in node:
            yield from _walk(child)


def _tokens(doc: dict[str, Any]) -> Iterator[tuple[int, dict[str, Any]]]:
    """Every `WFTextTokenString` in the document, with the index of the action holding it."""
    for index, action in enumerate(_actions(doc)):
        for node in _walk(_params(action)):
            if node.get("WFSerializationType") == "WFTextTokenString" and isinstance(node.get("Value"), dict):
                yield index, node["Value"]


def check_offsets(doc: dict[str, Any]) -> None:
    """Every attachment offset must land on a placeholder character, and vice versa."""
    for index, value in _tokens(doc):
        string = value.get("string", "")
        attachments = value.get("attachmentsByRange", {})
        if string.count(OBJ) != len(attachments):
            raise CheckError(
                f"action {index}: {string.count(OBJ)} placeholder characters but "
                f"{len(attachments)} attachments - an offset is wrong"
            )
        for key in attachments:
            offset = int(key.strip("{}").split(",")[0])
            if offset >= len(string) or string[offset] != OBJ:
                raise CheckError(
                    f"action {index}: attachment offset {offset} does not land on a "
                    f"placeholder character - the text and its variables disagree"
                )


def check_utf16_safe(doc: dict[str, Any]) -> None:
    """No text with attachments may contain a character outside the Basic Multilingual Plane.

    Shortcuts counts offsets in UTF-16 code units; Python counts code points.
    They agree until an emoji or other astral character appears, after which
    every later variable is one unit off. A token with no attachments is
    exempt: there is nothing to misplace.
    """
    for index, value in _tokens(doc):
        if not value.get("attachmentsByRange"):
            continue
        for character in value.get("string", ""):
            if ord(character) > 0xFFFF:
                raise CheckError(
                    f"action {index}: {character!r} is outside the Basic Multilingual "
                    f"Plane, so Python and UTF-16 offsets diverge - remove it"
                )


def check_no_dangling_references(doc: dict[str, Any]) -> None:
    """Every `OutputUUID` must name an action in this document."""
    known = {_params(action)["UUID"] for action in _actions(doc) if "UUID" in _params(action)}
    for index, action in enumerate(_actions(doc)):
        for node in _walk(_params(action)):
            referenced = node.get("OutputUUID")
            if referenced is not None and referenced not in known:
                raise CheckError(
                    f"action {index}: dangling reference to {referenced} - an action was reordered or removed"
                )


def _dictionary_keys(doc: dict[str, Any]) -> set[str]:
    """Every key defined by a Dictionary action in the document."""
    keys = set()
    for action in _actions(doc):
        if action.get("WFWorkflowActionIdentifier") != "is.workflow.actions.dictionary":
            continue
        items = _params(action).get("WFItems", {}).get("Value", {}).get("WFDictionaryFieldValueItems", [])
        for item in items:
            key = item.get("WFKey", {}).get("Value", {}).get("string")
            if key is not None:
                keys.add(key)
    return keys


def check_dictionary_keys(doc: dict[str, Any]) -> None:
    """Every `DictionaryKey` aggrandizement must name a key some Dictionary action defines.

    Rename a key in the Dictionary and forget the variable that reads it, and
    nothing else notices: the `OutputUUID` still resolves, so the reference
    check is satisfied, and the shortcut runs with an empty slot where the
    value was. The analogous check for `PropertyName` is impossible without a
    catalog of every content type's properties, so a mistyped property is
    still a silent empty slot.
    """
    defined = _dictionary_keys(doc)
    for index, action in enumerate(_actions(doc)):
        for node in _walk(_params(action)):
            if node.get("Type") != "WFDictionaryValueVariableAggrandizement":
                continue
            key = node.get("DictionaryKey")
            if key not in defined:
                raise CheckError(
                    f"action {index}: a variable reads dictionary key {key!r}, which no "
                    f"Dictionary action defines (defined: {sorted(defined)}) - the "
                    f"reference still resolves, so the slot is silently empty"
                )


def check_control_flow(doc: dict[str, Any]) -> None:
    """Every control-flow block must be opened once, closed once, by the same kind of action.

    A Repeat opened with mode 0 and closed by a conditional at mode 2 does not
    error, does not warn, and does not run its body even once; the actions
    between the markers are skipped, which looks exactly like a loop over an
    empty collection. Closing a Repeat with a Repeat and an If with an If is
    the rule. Blocks must also nest properly, an else-marker belongs only to
    an If or a menu, and a menu's items must match its cases one for one, or
    it imports cleanly and misroutes.
    """
    opened: dict[str, tuple[int, str]] = {}
    closed: set[str] = set()
    stack: list[str] = []
    menu_items: dict[str, list[str]] = {}
    menu_cases: dict[str, list[str]] = {}
    for index, action in enumerate(_actions(doc)):
        params = _params(action)
        group = params.get("GroupingIdentifier")
        if group is None:
            continue
        identifier = action.get("WFWorkflowActionIdentifier", "")
        mode = params.get("WFControlFlowMode")
        if mode == OPEN:
            if group in opened:
                raise CheckError(f"action {index}: group {group} opened twice")
            opened[group] = (index, identifier)
            stack.append(group)
            if identifier == MENU:
                menu_items[group] = list(params.get("WFMenuItems", []))
                menu_cases[group] = []
        elif mode == ELSE:
            if group not in opened or group in closed:
                raise CheckError(f"action {index}: else-marker for group {group}, which is not open")
            if identifier not in (CONDITIONAL, MENU):
                raise CheckError(f"action {index}: {identifier} has no middle marker; only If and Choose from Menu do")
            if identifier != opened[group][1]:
                raise CheckError(
                    f"action {index}: group {group} opened by {opened[group][1]} but has a {identifier} marker"
                )
            if stack[-1:] != [group]:
                raise CheckError(
                    f"action {index}: else-marker for group {group} while group {stack[-1] if stack else None} is open"
                )
            if identifier == MENU:
                menu_cases[group].append(params.get("WFMenuItemTitle", ""))
        elif mode == CLOSE:
            if group not in opened:
                raise CheckError(f"action {index}: group {group} closed but never opened")
            if group in closed:
                raise CheckError(f"action {index}: group {group} closed twice")
            if identifier != opened[group][1]:
                raise CheckError(
                    f"action {index}: group {group} opened by {opened[group][1]} at action "
                    f"{opened[group][0]} but closed by {identifier} - its body never runs"
                )
            if stack[-1:] != [group]:
                raise CheckError(
                    f"action {index}: group {group} closed while group {stack[-1] if stack else None} is still open"
                )
            stack.pop()
            closed.add(group)
        else:
            raise CheckError(f"action {index}: group {group} has an unknown control-flow mode {mode!r}")
    if stack:
        raise CheckError(f"group {stack[-1]} was opened and never closed")
    for group, items in menu_items.items():
        if items != menu_cases[group]:
            raise CheckError(
                f"menu {group}: items {items} do not match its cases {menu_cases[group]} - "
                f"a mismatch imports cleanly and misroutes at run time"
            )


def check_runs_shortcut(doc: dict[str, Any], known_names: Collection[str]) -> None:
    """Every Run Shortcut must name, twice and consistently, a shortcut in `known_names`.

    Run Shortcut resolves its target by name at run time, so a wrapper that
    names a shortcut which was renamed imports cleanly and then does nothing.
    Both the outer `WFWorkflowName` and the inner `workflowName` are checked,
    because both are written and it is not known which one the device reads.
    """
    for index, action in enumerate(_actions(doc)):
        if action.get("WFWorkflowActionIdentifier") != "is.workflow.actions.runworkflow":
            continue
        params = _params(action)
        outer = params.get("WFWorkflowName")
        inner = params.get("WFWorkflow", {}).get("workflowName")
        if outer != inner:
            raise CheckError(f"action {index}: Run Shortcut names {outer!r} outside and {inner!r} inside")
        if outer not in known_names:
            raise CheckError(
                f"action {index}: Run Shortcut targets {outer!r}, which is not one of "
                f"{sorted(known_names)} - it resolves by name, so this imports and then silently does nothing"
            )


def check_all(doc: dict[str, Any], *, known_shortcuts: Collection[str] | None = None) -> None:
    """Run every generic check. Raises `CheckError` on the first problem.

    `known_shortcuts` enables the Run Shortcut check; leave it `None` for a
    shortcut that is allowed to call things this build does not produce.
    """
    check_offsets(doc)
    check_utf16_safe(doc)
    check_no_dangling_references(doc)
    check_dictionary_keys(doc)
    check_control_flow(doc)
    if known_shortcuts is not None:
        check_runs_shortcut(doc, known_shortcuts)
