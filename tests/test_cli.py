"""The CLI handlers, driven with constructed namespaces against fake tools."""

from __future__ import annotations

import argparse
import os
import stat
import subprocess
import sys

import pytest

from shortcut_forge_cli.main import build_parser, handle_sign, handle_validate


@pytest.fixture
def bin_dir(tmp_path, monkeypatch):
    directory = tmp_path / "bin"
    directory.mkdir()
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv("SHORTCUT_FORGE_VALIDATOR", raising=False)
    monkeypatch.delenv("SHORTCUT_FORGE_SIGNER", raising=False)
    return directory


def tool(directory, name, script):
    path = directory / name
    path.write_text("#!/bin/sh\n" + script)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_parser_has_both_commands_and_a_version():
    parser = build_parser()
    args = parser.parse_args(["validate", "a.xml", "--waive", "x", "--waive", "y"])
    assert args.func is handle_validate
    assert args.waive == ["x", "y"]
    args = parser.parse_args(["sign", "a.xml", "--mode", "people-who-know-me"])
    assert args.func is handle_sign
    with pytest.raises(SystemExit):
        parser.parse_args(["--version"])


def test_validate_reports_waived_and_unexpected(bin_dir, tmp_path, capsys):
    tool(bin_dir, "validate-shortcut", 'echo "- Waived thing"\necho "- Real thing"\nexit 1\n')
    xml = tmp_path / "a.xml"
    xml.write_text("x")
    args = argparse.Namespace(xml=[xml], waive=["Waived"], target_macos="27", platform="ios")
    assert handle_validate(args) == 1
    out = capsys.readouterr().out
    assert "FAILED" in out
    assert "Real thing" in out
    assert "Waived thing" not in out

    tool(bin_dir, "validate-shortcut", 'echo "- Waived thing"\nexit 1\n')
    assert handle_validate(args) == 0
    assert "(1 waived)" in capsys.readouterr().out


def test_sign_names_the_output_after_the_stem(bin_dir, tmp_path, capsys):
    tool(
        bin_dir,
        "sign-shortcut",
        'out=""; name=""; while [ $# -gt 0 ]; do case "$1" in --output-dir) out="$2"; shift 2;; '
        '--name) name="$2"; shift 2;; *) shift;; esac; done\necho signed > "$out/$name.shortcut"\n',
    )
    xml = tmp_path / "Car Greetings.xml"
    xml.write_text("x")
    args = argparse.Namespace(xml=[xml], name=None, mode="anyone", output_dir=None)
    assert handle_sign(args) == 0
    assert (tmp_path / "Car Greetings.shortcut").exists()
    assert "signed ->" in capsys.readouterr().out


def test_sign_refuses_a_name_for_several_files(tmp_path):
    args = argparse.Namespace(xml=[tmp_path / "a.xml", tmp_path / "b.xml"], name="N", mode="anyone", output_dir=None)
    with pytest.raises(argparse.ArgumentError, match="single XML"):
        handle_sign(args)


def test_no_command_prints_help_and_exits_2():
    proc = subprocess.run([sys.executable, "-m", "shortcut_forge_cli"], capture_output=True, text=True, check=False)
    assert proc.returncode == 2
    assert "validate" in proc.stdout


def test_missing_tool_exits_1(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("SHORTCUT_FORGE_VALIDATOR", raising=False)
    xml = tmp_path / "a.xml"
    xml.write_text("x")
    proc = subprocess.run(
        [sys.executable, "-m", "shortcut_forge_cli", "validate", str(xml)], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 1
    assert "validate-shortcut is not on PATH" in proc.stdout + proc.stderr
