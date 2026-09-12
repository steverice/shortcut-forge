"""The primitives emit exactly the dictionaries the two generators used to write by hand."""

from __future__ import annotations

import plistlib

from shortcut_forge_lib.plist import (
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


def test_ts_places_offsets_at_the_placeholder_characters():
    token = ts("hello ", out("U1", "Text"), " world ", out("U2", "Text"))
    string = token["Value"]["string"]
    attachments = token["Value"]["attachmentsByRange"]

    assert token["WFSerializationType"] == "WFTextTokenString"
    assert string == f"hello {OBJ} world {OBJ}"
    assert set(attachments) == {"{6, 1}", "{14, 1}"}
    for key in attachments:
        offset = int(key.strip("{}").split(",")[0])
        assert string[offset] == OBJ


def test_ts_recomputes_offsets_when_a_literal_grows():
    short = ts("hi ", out("U1", "Text"))
    longer = ts("hello there ", out("U1", "Text"))
    assert list(short["Value"]["attachmentsByRange"]) == ["{3, 1}"]
    assert list(longer["Value"]["attachmentsByRange"]) == ["{12, 1}"]


def test_ts_of_a_bare_string_has_no_attachments():
    assert ts("plain") == {
        "Value": {"attachmentsByRange": {}, "string": "plain"},
        "WFSerializationType": "WFTextTokenString",
    }


def test_text_value_omits_the_attachment_map():
    assert text_value("k") == {"Value": {"string": "k"}, "WFSerializationType": "WFTextTokenString"}


def test_references():
    assert out("U", "Text") == {"OutputUUID": "U", "OutputName": "Text", "Type": "ActionOutput"}
    assert var("Direction") == {"Type": "Variable", "VariableName": "Direction"}
    assert EXTENSION_INPUT == {"Type": "ExtensionInput"}
    assert attach(var("X")) == {"Value": var("X"), "WFSerializationType": "WFTextTokenAttachment"}
    assert cond_input(var("X")) == {"Type": "Variable", "Variable": attach(var("X"))}


def test_prop_with_and_without_user_info():
    assert prop("U", "Weather Conditions", "Feels Like") == {
        **out("U", "Weather Conditions"),
        "Aggrandizements": [{"Type": "WFPropertyVariableAggrandizement", "PropertyName": "Feels Like"}],
    }
    assert prop("U", "City", "State", "state")["Aggrandizements"][0]["PropertyUserInfo"] == "state"


def test_dict_key():
    assert dict_key("U", "Dictionary", "car_model")["Aggrandizements"] == [
        {"Type": "WFDictionaryValueVariableAggrandizement", "DictionaryKey": "car_model"}
    ]


def test_dictionary_rows():
    assert kv("Accept", ts("application/json")) == {
        "WFItemType": 0,
        "WFKey": ts("Accept"),
        "WFValue": ts("application/json"),
    }
    assert kv_text("k", "v") == {"WFItemType": 0, "WFKey": text_value("k"), "WFValue": text_value("v")}
    nested = kv_dict("user", [kv("email", ts("e"))])
    assert nested["WFItemType"] == 1
    assert nested["WFValue"] == {
        "Value": dict_field([kv("email", ts("e"))]),
        "WFSerializationType": "WFDictionaryFieldValue",
    }
    assert dict_field([]) == {
        "Value": {"WFDictionaryFieldValueItems": []},
        "WFSerializationType": "WFDictionaryFieldValue",
    }


def test_act_and_comment():
    assert act("is.workflow.actions.nothing") == {
        "WFWorkflowActionIdentifier": "is.workflow.actions.nothing",
        "WFWorkflowActionParameters": {},
    }
    assert comment("hi") == act("is.workflow.actions.comment", WFCommentActionText="hi")
    assert comment("hi", "U")["WFWorkflowActionParameters"]["UUID"] == "U"


def test_import_question():
    assert import_question(3, "WFTextActionText", "Email?") == {
        "ActionIndex": 3,
        "Category": "Parameter",
        "DefaultValue": "",
        "ParameterKey": "WFTextActionText",
        "Text": "Email?",
    }


def test_document_root_keys():
    doc = document("Name", [comment("x")], glyph=1, color=2)
    assert doc["WFWorkflowName"] == "Name"
    assert doc["WFWorkflowClientVersion"] == "2700.0.4"
    assert doc["WFWorkflowMinimumClientVersion"] == 900
    assert doc["WFWorkflowMinimumClientVersionString"] == "900"
    assert doc["WFWorkflowIcon"] == {"WFWorkflowIconGlyphNumber": 1, "WFWorkflowIconStartColor": 2}
    assert doc["WFWorkflowImportQuestions"] == []
    assert doc["WFWorkflowTypes"] == []
    assert doc["WFWorkflowHasOutputFallback"] is False


def test_document_omits_input_classes_unless_given():
    """The validator rejects the key on a shortcut that never reads Shortcut Input."""
    assert "WFWorkflowInputContentItemClasses" not in document("N", [], glyph=1, color=2)
    assert document("N", [], glyph=1, color=2, input_classes=[])["WFWorkflowInputContentItemClasses"] == []
    assert document("N", [], glyph=1, color=2, input_classes=["WFStringContentItem"])[
        "WFWorkflowInputContentItemClasses"
    ] == ["WFStringContentItem"]


def test_document_without_a_name_matches_a_device_export():
    assert "WFWorkflowName" not in document(None, [], glyph=1, color=2)


def test_document_optional_export_keys():
    doc = document(
        "N",
        [],
        glyph=1,
        color=2,
        client_version="4402.0.1",
        workflow_types=["WFWorkflowTypeShowInSearch"],
        quick_action_surfaces=[],
        has_shortcut_input_variables=False,
    )
    assert doc["WFWorkflowClientVersion"] == "4402.0.1"
    assert doc["WFWorkflowTypes"] == ["WFWorkflowTypeShowInSearch"]
    assert doc["WFQuickActionSurfaces"] == []
    assert doc["WFWorkflowHasShortcutInputVariables"] is False


def test_write_xml_round_trips(tmp_path):
    doc = document("N", [comment("x")], glyph=1, color=2)
    path = write_xml(doc, tmp_path / "n.xml")
    assert path.read_bytes().startswith(b"<?xml")
    assert plistlib.loads(path.read_bytes()) == doc
    assert read_xml(path) == doc
