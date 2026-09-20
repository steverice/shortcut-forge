"""The setup-question canary: does answering a shortcut's setup question configure it?

Integration. It needs a device that is already booted — nothing here boots one —
named by `SHORTCUT_FORGE_SIM_UDID`, and the shortcuts-playground signer on PATH:

    xcrun simctl boot <udid>
    SHORTCUT_FORGE_SIM_UDID=<udid> make test-integ

The answer regressed during the iOS 27 beta cycle, which is why it is watched on
every runtime. Measured 2026-09-18: on iOS 27.0 (24A434) confirming the question
page installs nothing at all, and on iOS 27.2 beta 1 (24B5084k) it commits the
typed answer. When 27.2 ships as a release, re-measure and update the branch
below — and the matching note in brightwheel-checkin's `TESTING.md`.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import pytest

from shortcut_forge_lib.plist import write_xml
from shortcut_forge_lib.sim.harness import Simulator
from shortcut_forge_lib.sim.probes import SETUP_PROBE_PLACEHOLDER, ask_probe, setup_probe
from shortcut_forge_lib.toolchain import sign

pytestmark = pytest.mark.integration

ANSWER = "42424"  # five digits, deliberately: see below

# Digits because the answer field autocapitalizes letters — and five of them
# rather than six because `ask_probe` copies its answer to the clipboard, and
# a consumer's shortcut treats a six-digit clipboard as a login code it should
# offer instead of asking for one. A six-digit canary answer left on the device
# silently steers that suite down its clipboard path; measured 2026-09-20, when
# it cost two tests in the merge gate.


@pytest.fixture(scope="module")
def sim() -> Simulator:
    udid = os.environ.get("SHORTCUT_FORGE_SIM_UDID")
    if not udid:
        pytest.skip("set SHORTCUT_FORGE_SIM_UDID to an already-booted device")
    device = Simulator(udid, artifacts=Path("artifacts"))
    device.prepare()
    return device


def build(tmp_path: Path) -> tuple[str, Path]:
    """A fresh name each run: an installed copy would make the import a no-op."""
    name = f"ZZ Canary {uuid.uuid4().hex[:8]}"
    return name, sign(write_xml(setup_probe(name), tmp_path / f"{name}.xml"), name=name, output_dir=tmp_path)


def stored_text(sim: Simulator, name: str) -> str | None:
    actions = sim.shortcut_actions(name)
    return None if actions is None else actions[0]["WFWorkflowActionParameters"]["WFTextActionText"]


def test_skipping_setup_installs_the_placeholder(sim, tmp_path):
    name, path = build(tmp_path)
    assert sim.install(path, expect_name=name), f"{name} did not install"
    assert stored_text(sim, name) == SETUP_PROBE_PLACEHOLDER


def test_answering_the_setup_question(sim, tmp_path):
    name, path = build(tmp_path)
    assert sim.install(path, expect_name=name, skip_setup=False), "no setup question page appeared"
    assert sim.fill(ANSWER) == ANSWER
    sim.confirm("Add Shortcut", "Next")
    time.sleep(4)

    _device, version = sim.device_label()
    value = stored_text(sim, name)
    if version.startswith("27.0"):
        assert value is None, (
            f"iOS 27.0 used to install nothing when the question page was confirmed; this device stored {value!r}. "
            "If 27.0 has been fixed, that is a finding — record it before changing this assertion."
        )
    else:
        assert value == ANSWER, f"iOS {version} stored {value!r} instead of the typed answer"


def test_the_runners_dialogs_are_found_where_the_seeds_say(sim, tmp_path):
    """The seed table, exercised on a device instead of against a capture.

    Nothing else reaches it. The setup canary raises no consent, and the unit
    tests replay a capture recorded once — so without this, the positions the
    whole dialog tier depends on are proven only against the recording of
    themselves. `ask_probe` raises all three of the runner's dialogs.

    On a device that has already granted its consents, `clear_prompts` finds
    nothing and the test still proves the two seeds that matter most: the Ask
    field and its *Done*. Run it on an erased device to exercise the rest.
    """
    name = f"ZZ Ask {uuid.uuid4().hex[:8]}"
    path = sign(write_xml(ask_probe(name), tmp_path / f"{name}.xml"), name=name, output_dir=tmp_path)
    assert sim.install(path, expect_name=name), f"{name} did not install"
    sim.terminate_shortcuts()
    time.sleep(1.5)
    sim.run_shortcut(name)
    assert sim.answer_prompt(ANSWER), "no Ask dialog appeared where ASK_FIELD says it is"
    pressed = sim.clear_prompts()
    assert not sim.prompt_up(), f"a dialog is still up after clearing {pressed}"
