"""`build_all()`: write, check, validate, sign, in that order, stopping at the first problem."""

from __future__ import annotations

import os
import stat

import pytest

from shortcut_forge_lib.build import Shortcut, build_all
from shortcut_forge_lib.checks import CheckError
from shortcut_forge_lib.plist import act, comment, document, read_xml, ts, var
from shortcut_forge_lib.toolchain import ValidationError


def wrapper_for(name):
    return document(
        "Wrapper",
        [
            comment("w"),
            act(
                "is.workflow.actions.runworkflow",
                WFWorkflowName=name,
                WFWorkflow={"isSelf": False, "workflowIdentifier": "X", "workflowName": name},
            ),
        ],
        glyph=1,
        color=2,
    )


@pytest.fixture
def tools(tmp_path, monkeypatch):
    """A validator that passes and a signer that writes a marker, both counting their calls."""
    directory = tmp_path / "bin"
    directory.mkdir()
    log = tmp_path / "log"
    validator = directory / "validate-shortcut"
    validator.write_text(f'#!/bin/sh\necho "validate $1" >> "{log}"\necho "Validation passed."\n')
    signer = directory / "sign-shortcut"
    signer.write_text(
        f'#!/bin/sh\necho "sign $@" >> "{log}"\n'
        'out=""; name=""; while [ $# -gt 0 ]; do case "$1" in --output-dir) out="$2"; shift 2;; '
        '--name) name="$2"; shift 2;; *) shift;; esac; done\n'
        'echo signed > "$out/$name.shortcut"\n'
    )
    for tool in (validator, signer):
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv("SHORTCUT_FORGE_VALIDATOR", raising=False)
    monkeypatch.delenv("SHORTCUT_FORGE_SIGNER", raising=False)
    return log


def test_build_all_writes_validates_and_signs_in_order(tmp_path, tools):
    dist = tmp_path / "dist"
    main = document("Main", [comment("m")], glyph=1, color=2)
    steps = []
    signed = build_all(
        dist,
        [Shortcut("Main", main), Shortcut("Wrapper", wrapper_for("Main"), xml_stem="wrapper")],
        waived=["nothing"],
        on_step=steps.append,
    )
    assert signed == {"Main": dist / "Main.shortcut", "Wrapper": dist / "Wrapper.shortcut"}
    assert read_xml(dist / "Main.xml") == main
    assert (dist / "wrapper.xml").exists()
    log = tools.read_text().splitlines()
    assert [line.split()[0] for line in log] == ["validate", "validate", "sign", "sign"]
    assert "--mode anyone" in log[2]
    assert f"--output-dir {dist}" in log[2]
    assert steps == [
        "Main: wrote Main.xml",
        "Wrapper: wrote wrapper.xml",
        "Main: validated",
        "Wrapper: validated",
        f"Main: signed -> {dist / 'Main.shortcut'}",
        f"Wrapper: signed -> {dist / 'Wrapper.shortcut'}",
    ]


def test_unsigned_build_returns_the_xml_paths(tmp_path, tools):
    dist = tmp_path / "dist"
    out = build_all(dist, [Shortcut("Main", document("Main", [], glyph=1, color=2))], sign=False)
    assert out == {"Main": dist / "Main.xml"}
    assert [line.split()[0] for line in tools.read_text().splitlines()] == ["validate"]


def test_a_wrapper_naming_a_shortcut_outside_the_build_fails_the_checks(tmp_path, tools):
    with pytest.raises(CheckError, match="resolves by name"):
        build_all(tmp_path / "dist", [Shortcut("Wrapper", wrapper_for("Elsewhere"))], sign=False)
    build_all(
        tmp_path / "dist2", [Shortcut("Wrapper", wrapper_for("Elsewhere"))], sign=False, known_shortcuts=["Elsewhere"]
    )


def test_checks_run_before_anything_is_written(tmp_path, tools):
    dist = tmp_path / "dist"
    broken = document("Bad", [act("x", WFTextActionText=ts("a", var("V")))], glyph=1, color=2)
    broken["WFWorkflowActions"][0]["WFWorkflowActionParameters"]["WFTextActionText"]["Value"]["string"] = "ab"
    good = document("Good", [], glyph=1, color=2)
    with pytest.raises(CheckError):
        build_all(dist, [Shortcut("Good", good), Shortcut("Bad", broken)], sign=False)
    assert not tools.exists(), "the validator must not run when a check fails"


def test_validation_failure_stops_before_signing(tmp_path, tools, monkeypatch):
    validator = tmp_path / "bin" / "validate-shortcut"
    validator.write_text('#!/bin/sh\necho "- Something real"\nexit 1\n')
    with pytest.raises(ValidationError, match="Something real"):
        build_all(tmp_path / "dist", [Shortcut("Main", document("Main", [], glyph=1, color=2))])
    assert not tools.exists(), "the signer must not run when validation fails"


def test_checks_can_be_disabled(tmp_path, tools):
    broken = document("Bad", [act("x", WFTextActionText=ts("a", var("V")))], glyph=1, color=2)
    broken["WFWorkflowActions"][0]["WFWorkflowActionParameters"]["WFTextActionText"]["Value"]["string"] = "ab"
    build_all(tmp_path / "dist", [Shortcut("Bad", broken)], sign=False, run_checks=False)


def test_shortcut_xml_name():
    assert Shortcut("Car Greetings", {}).xml_name == "Car Greetings.xml"
    assert Shortcut("Car Greetings", {}, xml_stem="car-greetings").xml_name == "car-greetings.xml"
