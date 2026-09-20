"""The certificate, probe, and link modules: everything that runs without a simulator."""

from __future__ import annotations

import shutil
import ssl
import subprocess

import pytest

from shortcut_forge_lib.checks import check_all
from shortcut_forge_lib.plist import write_xml
from shortcut_forge_lib.sim import certs, links, probes
from shortcut_forge_lib.sim.links import LinkError, check_link, install_from_link

# -- probes ---------------------------------------------------------------------


def test_setup_probe_is_two_actions_with_one_question():
    doc = probes.setup_probe("Setup Canary")
    acts = doc["WFWorkflowActions"]
    assert [a["WFWorkflowActionIdentifier"] for a in acts] == [
        "is.workflow.actions.gettext",
        "is.workflow.actions.setclipboard",
    ]
    assert acts[0]["WFWorkflowActionParameters"]["WFTextActionText"] == probes.SETUP_PROBE_PLACEHOLDER
    question = doc["WFWorkflowImportQuestions"][0]
    assert question["ActionIndex"] == 0
    assert question["ParameterKey"] == "WFTextActionText"
    assert doc["WFWorkflowName"] == "Setup Canary"
    check_all(doc)


def test_ask_probe_asks_and_copies():
    doc = probes.ask_probe("Ask Canary")
    acts = doc["WFWorkflowActions"]
    assert [a["WFWorkflowActionIdentifier"] for a in acts] == [
        "is.workflow.actions.ask",
        "is.workflow.actions.setclipboard",
    ]
    ask = acts[0]["WFWorkflowActionParameters"]
    assert ask["WFInputType"] == "Text"
    assert "digits" in ask["WFAskActionPrompt"]
    # The clipboard action carries the Ask action's output, which is what makes
    # the answer readable from outside the device.
    pasted = acts[1]["WFWorkflowActionParameters"]["WFInput"]["Value"]
    assert pasted["OutputUUID"] == ask["UUID"]
    assert pasted["OutputName"] == "Provided Input"
    assert doc["WFWorkflowImportQuestions"] == []
    check_all(doc)


# -- certs ----------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("openssl") is None, reason="needs openssl")
def test_ensure_certs_makes_a_chain_ios_will_accept(tmp_path):
    ca, server = certs.ensure_certs(tmp_path / "tls", ca_name="Probe CA")
    assert ca.exists()
    assert server.exists()
    text = subprocess.run(
        ["openssl", "x509", "-in", str(tmp_path / "tls" / "server.crt"), "-noout", "-text"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "DNS:localhost" in text
    assert "TLS Web Server Authentication" in text
    ca_text = subprocess.run(
        ["openssl", "x509", "-in", str(ca), "-noout", "-subject"], capture_output=True, text=True, check=True
    ).stdout
    assert "Probe CA" in ca_text
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(server)  # key and cert in one file, the shape a server wants
    # A second call is a no-op; a forced one regenerates.
    first = server.read_text()
    assert certs.ensure_certs(tmp_path / "tls") == (ca, server)
    assert server.read_text() == first
    certs.ensure_certs(tmp_path / "tls", force=True)
    assert server.read_text() != first


# -- links ----------------------------------------------------------------------


class FakeSim:
    """Shows no import sheet until the link has been opened `works_on` times."""

    udid = "FAKE"

    def __init__(self, works_on=1, installed=(), actions=None, questions=0):
        self.works_on = works_on
        self.opens = 0
        self.tapped = False
        self._installed = list(installed)
        self._actions = actions
        self.questions = questions

    def terminate_shortcuts(self):
        pass

    def blue_buttons(self, img=None):
        return [(0, 0, 10, 10)] if self.opens >= self.works_on and not self.tapped else []

    def tap_affirmative(self, img=None):
        self.tapped = True
        return True

    def library(self):
        return self._installed

    def shortcut_actions(self, name):
        return self._actions


@pytest.fixture
def fast(monkeypatch):
    """No sleeping, and `xcrun simctl openurl` counts opens instead of running."""
    monkeypatch.setattr(links.time, "sleep", lambda *_: None)
    opened = []

    def fake_run(argv, check=False):
        opened.append(argv)
        sim = fake_run.sim
        sim.opens += 1
        return subprocess.CompletedProcess(argv, 0)

    fake_run.sim = None
    monkeypatch.setattr(links.subprocess, "run", fake_run)
    return fake_run


def test_link_that_works_on_the_first_open(fast):
    sim = FakeSim(works_on=1)
    fast.sim = sim
    install_from_link(sim, "https://www.icloud.com/shortcuts/abc")
    assert sim.opens == 1
    assert sim.tapped


def test_link_that_needs_a_second_open(fast):
    """The miss that happened once on a freshly erased simulator."""
    sim = FakeSim(works_on=2)
    fast.sim = sim
    install_from_link(sim, "https://www.icloud.com/shortcuts/abc", timeout=0.05)
    assert sim.opens == 2
    assert sim.tapped


def test_link_that_never_shows_a_sheet(fast):
    sim = FakeSim(works_on=3)
    fast.sim = sim
    with pytest.raises(LinkError, match="either try"):
        install_from_link(sim, "https://www.icloud.com/shortcuts/abc", timeout=0.05)
    assert sim.opens == 2
    assert not sim.tapped


def test_check_link_compares_actions_and_questions(fast, tmp_path, monkeypatch, car_greetings):
    xml = write_xml(car_greetings, tmp_path / "car-greetings.xml")
    installed = car_greetings["WFWorkflowActions"]
    monkeypatch.setattr(links, "question_count", lambda sim, name: sim.questions)

    def after_install(sim):
        sim._installed.append("Car Greetings")

    good = FakeSim(actions=installed)
    fast.sim = good
    monkeypatch.setattr(links, "install_from_link", lambda sim, link, **kw: after_install(sim))
    assert check_link(good, "Car Greetings", "link", xml) == []

    fewer = FakeSim(actions=installed[:-1], questions=2)
    fast.sim = fewer
    problems = check_link(fewer, "Car Greetings", "link", xml)
    assert any("actions differ" in p for p in problems)
    assert any("import questions: 2 on the link, 0 built" in p for p in problems)


def test_check_link_refuses_when_already_installed(tmp_path, car_greetings):
    xml = write_xml(car_greetings, tmp_path / "x.xml")
    problems = check_link(FakeSim(installed=["Car Greetings"]), "Car Greetings", "link", xml)
    assert "already installed" in problems[0]


def test_check_link_needs_a_build(tmp_path):
    assert "build first" in check_link(FakeSim(), "X", "link", tmp_path / "missing.xml")[0]
