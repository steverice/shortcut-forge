"""`validate()` and `sign()` against fake tools on a temporary PATH. No plugin, no network."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from shortcut_forge import toolchain
from shortcut_forge.toolchain import SigningError, ToolNotFoundError, sign, validate


def fake_tool(directory: Path, name: str, script: str) -> Path:
    path = directory / name
    path.write_text("#!/bin/sh\n" + script)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def bin_dir(tmp_path, monkeypatch):
    directory = tmp_path / "bin"
    directory.mkdir()
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv("SHORTCUT_FORGE_VALIDATOR", raising=False)
    monkeypatch.delenv("SHORTCUT_FORGE_SIGNER", raising=False)
    return directory


@pytest.fixture
def xml(tmp_path):
    path = tmp_path / "dist" / "Thing.xml"
    path.parent.mkdir()
    path.write_text("<plist/>")
    return path


def test_missing_tool_is_a_clear_error(tmp_path, monkeypatch, xml):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("SHORTCUT_FORGE_VALIDATOR", raising=False)
    with pytest.raises(ToolNotFoundError, match="validate-shortcut"):
        validate(xml)


def test_env_override_wins(tmp_path, monkeypatch, xml):
    tool = fake_tool(tmp_path, "my-validator", 'echo "Validation passed."\n')
    monkeypatch.setenv("SHORTCUT_FORGE_VALIDATOR", str(tool))
    assert validate(xml).ok


def test_validate_passes_target_flags_and_reports_clean(bin_dir, xml):
    fake_tool(bin_dir, "validate-shortcut", 'echo "args: $@"\necho "Validation passed."\n')
    report = validate(xml, target_macos="26", platform="macos")
    assert report.ok
    assert report.status == 0
    assert report.errors == []
    assert f"args: {xml} --target-macos 26 --target-platform macos" in report.report


def test_waivers_filter_by_regex_and_the_rest_is_unexpected(bin_dir, xml):
    fake_tool(
        bin_dir,
        "validate-shortcut",
        'echo "- Second Comment missing required Shortcuts Playground prompt text"\n'
        'echo "- WFWorkflowIconGlyphNumber 62021 is not in the official mapping"\n'
        'echo "- Something real"\n'
        "exit 1\n",
    )
    report = validate(
        xml, waived=["Shortcuts Playground prompt text", r"WFWorkflowIconGlyphNumber (62021|62022) is not"]
    )
    assert len(report.errors) == 3
    assert report.unexpected == ["- Something real"]
    assert not report.ok


def test_non_zero_exit_with_nothing_itemized_is_unexpected(bin_dir, xml):
    """The car-greetings fix: an unreadable plist produced no bullets and used to pass the gate."""
    fake_tool(bin_dir, "validate-shortcut", 'echo "Failed to read plist: Invalid file"\nexit 2\n')
    report = validate(xml)
    assert not report.ok
    assert "exited 2 without itemizing anything" in report.unexpected[0]
    assert "Invalid file" in report.unexpected[0]


def test_sign_defaults_output_dir_to_the_xml_directory(bin_dir, xml):
    fake_tool(
        bin_dir,
        "sign-shortcut",
        'out=""; name=""; while [ $# -gt 0 ]; do case "$1" in --output-dir) out="$2"; shift 2;; '
        '--name) name="$2"; shift 2;; --mode) echo "mode=$2"; shift 2;; *) shift;; esac; done\n'
        'echo signed > "$out/$name.shortcut"\n',
    )
    signed = sign(xml, name="Thing")
    assert signed == xml.parent / "Thing.shortcut"
    assert signed.read_text() == "signed\n"


def test_sign_passes_mode_and_honors_output_dir(bin_dir, xml, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    fake_tool(
        bin_dir,
        "sign-shortcut",
        'out=""; name=""; while [ $# -gt 0 ]; do case "$1" in --output-dir) out="$2"; shift 2;; '
        '--name) name="$2"; shift 2;; --mode) mode="$2"; shift 2;; *) shift;; esac; done\n'
        'echo "$mode" > "$out/$name.shortcut"\n',
    )
    signed = sign(xml, name="Thing", mode="people-who-know-me", output_dir=elsewhere)
    assert signed == elsewhere / "Thing.shortcut"
    assert signed.read_text().strip() == "people-who-know-me"


def test_sign_retries_once_then_succeeds(bin_dir, xml, tmp_path):
    marker = tmp_path / "tried"
    fake_tool(
        bin_dir,
        "sign-shortcut",
        f'if [ ! -e "{marker}" ]; then touch "{marker}"; echo "Failed to modify some records" >&2; exit 1; fi\n'
        'out=""; name=""; while [ $# -gt 0 ]; do case "$1" in --output-dir) out="$2"; shift 2;; '
        '--name) name="$2"; shift 2;; *) shift;; esac; done\n'
        'echo signed > "$out/$name.shortcut"\n',
    )
    assert sign(xml, name="Thing").exists()


def test_sign_gives_up_after_the_attempts(bin_dir, xml):
    fake_tool(bin_dir, "sign-shortcut", 'echo "500" >&2\nexit 1\n')
    with pytest.raises(SigningError, match=r"failed 2 times.*500"):
        sign(xml, name="Thing")


def test_sign_notices_a_silent_no_op(bin_dir, xml):
    fake_tool(bin_dir, "sign-shortcut", "exit 0\n")
    with pytest.raises(SigningError, match="does not exist"):
        sign(xml, name="Thing")


def test_defaults_are_ios_27():
    assert toolchain.DEFAULT_TARGET_MACOS == "27"
    assert toolchain.DEFAULT_PLATFORM == "ios"
