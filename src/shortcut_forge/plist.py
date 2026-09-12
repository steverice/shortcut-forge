"""The plist shapes a Shortcut is made of.

Every helper here returns exactly the dictionary Shortcuts serializes for that
construct; nothing is abstracted past the plist. That is deliberate: the
consumers of this library have been bitten by every documented shape that
turned out to be wrong, so a generator should be able to see the plist it is
writing. The higher-level idioms live in `actions.py`.

The one real piece of work is `ts()`. An inline variable in Shortcuts text is a
U+FFFC placeholder character whose attachment is keyed by its character offset
into the finished string. `ts()` derives those offsets, so rewording a literal
moves every later variable by itself.
"""

from __future__ import annotations

import plistlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path

OBJ = "￼"
"""The OBJECT REPLACEMENT CHARACTER that stands in for one inline variable."""

EXTENSION_INPUT: dict[str, str] = {"Type": "ExtensionInput"}
"""The attachment value for Shortcut Input, the value the caller handed in."""

CLIENT_VERSION = "2700.0.4"
"""The `WFWorkflowClientVersion` this library writes unless told otherwise.

Read off an iOS 27 export; the other value seen in the wild is `4402.0.1`.
Neither has ever made a difference to an import.
"""


def ts(*parts: str | dict[str, Any]) -> dict[str, Any]:
    """A `WFTextTokenString` from interleaved literals and attachments.

    Strings are literal text. Anything else is an attachment value, such as
    `out()`, `var()`, `prop()`, `dict_key()`, or `EXTENSION_INPUT`, and is
    placed at the offset the text has reached.

    Offsets are counted in Python code points. Shortcuts counts UTF-16 code
    units, and the two agree only inside the Basic Multilingual Plane, so an
    emoji in a literal shifts every later variable. `checks.check_utf16_safe`
    catches that.
    """
    string, attachments = "", {}
    for part in parts:
        if isinstance(part, str):
            string += part
        else:
            attachments[f"{{{len(string)}, 1}}"] = part
            string += OBJ
    return {
        "Value": {"attachmentsByRange": attachments, "string": string},
        "WFSerializationType": "WFTextTokenString",
    }


def text_value(string: str) -> dict[str, Any]:
    """A `WFTextTokenString` carrying literal text and no attachments.

    The shape a Dictionary action writes for its keys and values. `ts()` writes
    an empty `attachmentsByRange` alongside, which Shortcuts also accepts; this
    one matches an export byte for byte.
    """
    return {"Value": {"string": string}, "WFSerializationType": "WFTextTokenString"}


def out(action_uuid: str, name: str) -> dict[str, Any]:
    """A reference to another action's output, by that action's UUID."""
    return {"OutputUUID": action_uuid, "OutputName": name, "Type": "ActionOutput"}


def var(name: str) -> dict[str, Any]:
    """A reference to a named variable, including the built-in `Repeat Item`."""
    return {"Type": "Variable", "VariableName": name}


def prop(action_uuid: str, name: str, property_name: str, user_info: str | None = None) -> dict[str, Any]:
    """An output reference narrowed to one of its properties.

    `property_name` is the label Shortcuts shows, such as `Feels Like` or
    `City`; some properties also carry a `PropertyUserInfo` key, which is what
    `user_info` sets. No catalog of valid property names exists, so a typo here
    is a silent empty slot at run time.
    """
    aggrandizement: dict[str, str] = {
        "Type": "WFPropertyVariableAggrandizement",
        "PropertyName": property_name,
    }
    if user_info is not None:
        aggrandizement["PropertyUserInfo"] = user_info
    return {**out(action_uuid, name), "Aggrandizements": [aggrandizement]}


def dict_key(action_uuid: str, name: str, key: str) -> dict[str, Any]:
    """An output reference narrowed to one key of a dictionary output."""
    return {
        **out(action_uuid, name),
        "Aggrandizements": [{"Type": "WFDictionaryValueVariableAggrandizement", "DictionaryKey": key}],
    }


def attach(value: dict[str, Any]) -> dict[str, Any]:
    """A `WFTextTokenAttachment`: a whole value passed as one parameter."""
    return {"Value": value, "WFSerializationType": "WFTextTokenAttachment"}


def cond_input(value: dict[str, Any]) -> dict[str, Any]:
    """The `WFInput` of an If action: a Variable wrapper around an attachment."""
    return {"Type": "Variable", "Variable": attach(value)}


def dict_field(items: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """A `WFDictionaryFieldValue`: the rows of a Dictionary, a header list, or a JSON body."""
    return {
        "Value": {"WFDictionaryFieldValueItems": list(items)},
        "WFSerializationType": "WFDictionaryFieldValue",
    }


def kv(key: str, value: dict[str, Any]) -> dict[str, Any]:
    """One text row of a dictionary field. `value` is a `ts()` or `text_value()`."""
    return {"WFItemType": 0, "WFKey": ts(key), "WFValue": value}


def kv_dict(key: str, items: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """One row of a dictionary field whose value is itself a dictionary."""
    return {
        "WFItemType": 1,
        "WFKey": ts(key),
        "WFValue": {"Value": dict_field(items), "WFSerializationType": "WFDictionaryFieldValue"},
    }


def act(identifier: str, **params: Any) -> dict[str, Any]:
    """One action. `params` are its `WFWorkflowActionParameters`, verbatim."""
    return {"WFWorkflowActionIdentifier": identifier, "WFWorkflowActionParameters": params}


def comment(text: str, action_uuid: str | None = None) -> dict[str, Any]:
    """A Comment action.

    Never put an internal `WF*` parameter name in the text: the validator
    rejects it and asks for the wording the Shortcuts editor shows.
    """
    params: dict[str, Any] = {"WFCommentActionText": text}
    if action_uuid is not None:
        params["UUID"] = action_uuid
    return act("is.workflow.actions.comment", **params)


def import_question(action_index: int, parameter_key: str, text: str, default: str = "") -> dict[str, Any]:
    """One setup question, asked when the shortcut is imported.

    The answer is written into `parameter_key` of the action at
    `action_index`. `text` is the only prose slot on the whole import flow, so
    warnings about the import itself end up in here; it takes `\\n\\n` for a
    paragraph break.
    """
    return {
        "ActionIndex": action_index,
        "Category": "Parameter",
        "DefaultValue": default,
        "ParameterKey": parameter_key,
        "Text": text,
    }


def document(
    name: str,
    actions: Sequence[dict[str, Any]],
    *,
    glyph: int,
    color: int,
    questions: Iterable[dict[str, Any]] = (),
    input_classes: Sequence[str] | None = None,
    output_classes: Sequence[str] = (),
    workflow_types: Sequence[str] = (),
    client_version: str = CLIENT_VERSION,
    minimum_client_version: int = 900,
    quick_action_surfaces: Sequence[str] | None = None,
    has_shortcut_input_variables: bool | None = None,
) -> dict[str, Any]:
    """The root dictionary of a shortcut file.

    `glyph` and `color` are the icon: a glyph number and a palette key, and
    nothing else, because there is no custom-image escape hatch. Both are
    device-measured numbers, not names; see `docs/building-shortcuts.md`.

    `input_classes=None` omits `WFWorkflowInputContentItemClasses` entirely.
    That matters: the validator rejects the key on a shortcut that never reads
    Shortcut Input, and accepts an empty list on one that does not.
    `quick_action_surfaces` and `has_shortcut_input_variables` are likewise
    omitted when `None`; exports differ in whether they carry them.
    """
    doc: dict[str, Any] = {
        "WFWorkflowActions": list(actions),
        "WFWorkflowClientVersion": client_version,
        "WFWorkflowHasOutputFallback": False,
        "WFWorkflowIcon": {"WFWorkflowIconGlyphNumber": glyph, "WFWorkflowIconStartColor": color},
        "WFWorkflowImportQuestions": list(questions),
        "WFWorkflowMinimumClientVersion": minimum_client_version,
        "WFWorkflowMinimumClientVersionString": str(minimum_client_version),
        "WFWorkflowName": name,
        "WFWorkflowOutputContentItemClasses": list(output_classes),
        "WFWorkflowTypes": list(workflow_types),
    }
    if input_classes is not None:
        doc["WFWorkflowInputContentItemClasses"] = list(input_classes)
    if quick_action_surfaces is not None:
        doc["WFQuickActionSurfaces"] = list(quick_action_surfaces)
    if has_shortcut_input_variables is not None:
        doc["WFWorkflowHasShortcutInputVariables"] = has_shortcut_input_variables
    return doc


def write_xml(doc: dict[str, Any], path: Path) -> Path:
    """Write a document as an XML plist. Keys are sorted, so output is stable."""
    path.write_bytes(plistlib.dumps(doc, fmt=plistlib.FMT_XML))
    return path


def read_xml(path: Path) -> dict[str, Any]:
    """Read an XML or binary plist back into a document."""
    with path.open("rb") as handle:
        return plistlib.load(handle)
