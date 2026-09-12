"""Generate, check, sign, and simulator-test iOS Shortcuts from Python.

The plist primitives are re-exported here for a generator that wants one
import line; everything else is reached through its module.
"""

from __future__ import annotations

from shortcut_forge.actions import GREATER_THAN, LESS_THAN, ActionList
from shortcut_forge.build import Shortcut, build_all
from shortcut_forge.checks import CheckError, check_all
from shortcut_forge.plist import (
    EXTENSION_INPUT,
    OBJ,
    act,
    attach,
    comment,
    cond_input,
    dict_field,
    dict_key,
    document,
    import_question,
    kv,
    kv_dict,
    kv_text,
    out,
    prop,
    read_xml,
    text_value,
    ts,
    var,
    write_xml,
)
from shortcut_forge.uuids import RoleUuids, random_uuids

__all__ = [
    "EXTENSION_INPUT",
    "GREATER_THAN",
    "LESS_THAN",
    "OBJ",
    "ActionList",
    "CheckError",
    "RoleUuids",
    "Shortcut",
    "act",
    "attach",
    "build_all",
    "check_all",
    "comment",
    "cond_input",
    "dict_field",
    "dict_key",
    "document",
    "import_question",
    "kv",
    "kv_dict",
    "kv_text",
    "out",
    "prop",
    "random_uuids",
    "read_xml",
    "text_value",
    "ts",
    "var",
    "write_xml",
]
