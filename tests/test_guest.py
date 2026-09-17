"""The guest module's pure parts.

Every assertion here is a finding that cost something to establish, written as a
test so a later edit cannot quietly undo it. Nothing boots a VM. The measurements
behind each one are in `docs/macos-guest.md`.
"""

from __future__ import annotations

import plistlib
import sqlite3
from pathlib import Path

import pytest

from shortcut_forge_lib.guest import bake, bless, ssh, tart, vnc

PROV = tart.Provisioning(full_name="Guest Probe", username="probe", password="s3cret")


# -- tart argv ---------------------------------------------------------------


def test_run_uses_screen_sharing_not_the_crash_prone_server():
    """`--vnc-experimental` renders but took the VM down twice with SIGTRAP."""
    args = tart.run_args("g")
    assert "--vnc" in args
    assert "--vnc-experimental" not in args


def test_run_never_passes_no_graphics():
    """It removes the display device: no WindowServer, and GUI apps cannot launch."""
    assert "--no-graphics" not in tart.run_args("g")


def test_provisioning_opts_is_one_comma_separated_list_of_five_keys():
    opts = tart.provisioning_opts(PROV)
    assert opts.count("=") == 5
    assert {part.split("=", 1)[0] for part in opts.split(",")} == {
        "fullName",
        "username",
        "password",
        "logsInAutomatically",
        "enablesRemoteLogin",
    }


def test_provisioning_booleans_are_the_literal_strings():
    """Anything but `true`/`false` is rejected by tart."""
    opts = tart.provisioning_opts(PROV)
    assert "logsInAutomatically=true" in opts
    assert "enablesRemoteLogin=true" in opts
    assert tart.provisioning_opts(tart.Provisioning("n", "u", "p", enables_remote_login=False)).endswith("false")


def test_run_args_carry_provisioning_and_shares():
    args = tart.run_args("g", provisioning=PROV, dir_shares={"probes": "/tmp/p"})
    assert "--provisioning-opts" in args
    assert "--dir=probes:/tmp/p" in args


# -- the blessing ------------------------------------------------------------


def test_two_services_are_granted_not_one():
    """Capture and input are separate grants. With only ScreenCapture the guest
    renders perfectly and ignores every click."""
    assert set(bless.GRANTED_SERVICES) == {"kTCCServiceScreenCapture", "kTCCServicePostEvent"}


def test_rows_are_allow_and_carry_a_real_csreq():
    rows = bless.tcc_rows()
    assert len(rows) == 2
    for row in rows:
        assert row["service"] in bless.GRANTED_SERVICES
        assert row["client"] == bless.SCREEN_SHARING_AGENT
        assert row["auth_value"] == 2, "2 is 'allowed'"
        # A missing or wrong csreq dumps as a perfectly correct row and is
        # ignored at authorization time.
        assert isinstance(row["csreq"], bytes)
        assert row["csreq"]


def test_the_csreq_pins_the_bundle_id_and_apple_anchor():
    """Why it is portable: identity plus anchor, nothing hardware-bound.

    A third-party client's requirement pins to the vendor's signature instead
    and is not a constant — see the doc before reusing this technique for one.
    """
    assert bless.SCREEN_SHARING_AGENT.encode() in bless.CSREQ


def test_mount_uses_noowners_so_no_host_root_is_needed():
    args = bless.mount_args("/dev/disk9s5", "/tmp/mnt")
    assert "noowners" in args
    assert args[:3] == ["mount", "-t", "apfs"]


def test_attach_does_not_mount():
    """`-nomount` first; the Data volume is mounted deliberately afterwards."""
    assert "-nomount" in bless.attach_args("/x/disk.img")


def test_data_volume_is_picked_by_name():
    devices = [("/dev/disk9s1", "ISC"), ("/dev/disk9s5", "Data"), ("/dev/disk9s3", "Recovery")]
    assert bless.data_volume(devices) == "/dev/disk9s5"


def test_data_volume_returns_none_when_absent():
    assert bless.data_volume([("/dev/disk9s1", "ISC")]) is None


def test_scale_is_set_to_one():
    """A guest at 2x HiDPI renders flawlessly and drops every pointer event."""
    args = bless.scale_args("/mnt/x.plist")
    assert args[-1] == "/mnt/x.plist"
    assert any("CurrentInfo:Scale 1" in a for a in args)


# -- verification predicates -------------------------------------------------


@pytest.mark.parametrize(("colors", "expected"), [(1, False), (2, False), (3, True), (52465, True)])
def test_rendered_rejects_a_flat_frame(colors, expected):
    """A denied capture returns a well-formed frame of exactly one color."""
    assert bless.rendered(colors) is expected


# -- reading tart's own state ------------------------------------------------

LISTING = """[
  {"Source": "local", "Name": "bw-mint-virgin", "State": "stopped", "Disk": 50,
   "Accessed": "2026-09-16T23:54:31Z", "Size": 34, "Running": false},
  {"Source": "local", "Name": "bw-mint-demo", "State": "running", "Disk": 50,
   "Accessed": "2026-09-16T23:58:02Z", "Size": 36, "Running": true}
]"""


def test_guests_reads_the_json_listing():
    assert tart.guests(LISTING) == {"bw-mint-virgin": "stopped", "bw-mint-demo": "running"}


def test_the_listing_is_asked_for_as_json():
    """The text table's `Accessed` column is free text, so its field count
    varies per row and a positional parser reads the wrong thing on some."""
    assert tart.list_args() == ["list", "--format", "json"]


def test_an_empty_listing_is_no_guests_not_a_crash():
    assert tart.guests("") == {}


def test_disk_image_follows_tart_home(monkeypatch):
    monkeypatch.setenv("TART_HOME", "/somewhere/.tart")
    assert tart.disk_image("g") == Path("/somewhere/.tart/vms/g/disk.img")


# -- the VNC client ----------------------------------------------------------


def test_server_arg_doubles_the_colon():
    """One colon is a display number, not a port, and connects to the wrong thing."""
    assert vnc.server_arg("10.0.0.5") == "10.0.0.5::5900"


def test_auth_always_carries_a_username():
    """Screen Sharing speaks ARD. Given only a password, vncdo prompts and hangs."""
    assert vnc.auth_args("probe", "pw")[:2] == ["--username", "probe"]
    assert "--username" in vnc.capture_args("h", "probe", "pw", "/tmp/x.png")
    assert "--username" in vnc.click_args("h", "probe", "pw", 1, 2)


def test_a_click_moves_first():
    """A click without a move lands wherever the pointer already was."""
    args = vnc.click_args("h", "probe", "pw", 40, 50)
    assert args.index("move") < args.index("click")
    assert args[args.index("move") + 1 : args.index("move") + 3] == ["40", "50"]


# -- SSH ---------------------------------------------------------------------


def test_the_password_never_reaches_an_argv(tmp_path):
    args = ssh.ssh_args("10.0.0.5", "probe", tmp_path / "known_hosts")
    assert "hunter2" not in " ".join(args)
    with ssh.askpass(tmp_path, "hunter2") as env:
        helper = Path(env["SSH_ASKPASS"])
        assert "hunter2" in helper.read_text()
        assert helper.stat().st_mode & 0o777 == 0o700
    assert not helper.exists(), "the helper is removed when the call is over"


def test_known_hosts_is_per_call(tmp_path):
    """A fresh guest brings a new key on a recycled address; the real
    known_hosts would refuse the second guest."""
    args = ssh.ssh_args("10.0.0.5", "probe", tmp_path / "known_hosts")
    assert f"UserKnownHostsFile={tmp_path / 'known_hosts'}" in args


# -- the bake ----------------------------------------------------------------


def test_preconditions_names_every_problem_at_once():
    """Twenty minutes into a restore is the wrong moment to learn the second one."""
    problems = bake.preconditions(
        "taken", running=2, existing=["taken"], host_major=26, free_bytes=0, missing=["/nope/tart"]
    )
    joined = " ".join(problems)
    assert "macOS 26" in joined
    assert "/nope/tart" in joined
    assert "taken" in joined
    assert "already running" in joined
    assert "0 GB free" in joined


def test_preconditions_reads_nothing_of_its_own():
    """Every fact arrives as an argument, so CI's own host cannot decide the
    answer — it did, twice, when this measured free space and PATH itself."""
    assert (
        bake.preconditions(
            "fresh",
            running=0,
            existing=["other"],
            host_major=27,
            free_bytes=bake.NEEDED_BYTES,
            missing=[],
        )
        == []
    )


def test_missing_tools_names_the_tart_binary_it_was_given():
    assert "/nope/tart" in bake.missing_tools("/nope/tart")


# -- writing the grants twice ------------------------------------------------

#: `access` as macOS 27.0 declares it, read verbatim out of a guest's store. The
#: primary key is the reason this file carries a schema at all: it is four
#: columns and does **not** include `indirect_object_identifier_type`, so the
#: NULL `bless.tcc_rows()` writes there cannot defeat the unique index. On a
#: schema whose key did include that column, NULLs compare distinct, every
#: `INSERT OR REPLACE` would degrade to a plain insert, and a second blessing
#: would double the rows instead of overriding them. The foreign key to
#: `policies` is dropped; nothing here exercises it.
TCC_SCHEMA = """
CREATE TABLE access (
    service TEXT NOT NULL,
    client TEXT NOT NULL,
    client_type INTEGER NOT NULL,
    auth_value INTEGER NOT NULL,
    auth_reason INTEGER NOT NULL,
    auth_version INTEGER NOT NULL,
    csreq BLOB,
    policy_id INTEGER,
    indirect_object_identifier_type INTEGER,
    indirect_object_identifier TEXT NOT NULL DEFAULT 'UNUSED',
    indirect_object_code_identity BLOB,
    flags INTEGER,
    last_modified INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER)),
    pid INTEGER,
    pid_version INTEGER,
    boot_uuid TEXT NOT NULL DEFAULT 'UNUSED',
    last_reminded INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER)),
    one_time_reprompt_eligible INTEGER,
    reminder_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (service, client, client_type, indirect_object_identifier)
)
"""


def _store(tmp_path):
    con = sqlite3.connect(tmp_path / "TCC.db")
    con.execute(TCC_SCHEMA)
    return con


def _grants(con):
    return con.execute("SELECT count(*) FROM access WHERE client = ?", (bless.SCREEN_SHARING_AGENT,)).fetchone()[0]


def test_blessing_twice_overrides_rather_than_doubles(tmp_path):
    """Re-blessing an image that already carries the grants must leave two rows."""
    con = _store(tmp_path)
    for _ in range(3):
        con.executemany(bless.insert_sql(), [bless.row_values(row) for row in bless.tcc_rows()])
    assert _grants(con) == 2


def test_a_denial_is_overridden_not_joined(tmp_path):
    """A store can carry these rows already, set to denied. A fresh guest does
    not — it has no `com.apple.screensharing.agent` row at all — but a Mac that
    has refused Screen Sharing does, and a blessing there overrides rather than
    fills a gap.

    The existing row is given the other `indirect_object_identifier_type`, since
    the OS uses both across the table. The key ignores that column, so the grant
    still lands on top rather than beside — two rows claiming the same service,
    with no way to say which wins at authorization time.
    """
    con = _store(tmp_path)
    existing = [dict(row, indirect_object_identifier_type=None, auth_value=0) for row in bless.tcc_rows()]
    con.executemany(bless.insert_sql(), [bless.row_values(row) for row in existing])
    con.executemany(bless.insert_sql(), [bless.row_values(row) for row in bless.tcc_rows()])
    assert _grants(con) == 2
    assert con.execute("SELECT DISTINCT auth_value FROM access").fetchall() == [(2,)], "the denial was overridden"


def test_the_probe_point_scales_with_the_framebuffer():
    """Held as a fraction so a guest at another resolution still hits the Dock."""
    assert bake.probe_point(1024, 768) == (150, 740)
    x, y = bake.probe_point(2048, 1536)
    assert (x, y) == (300, 1480)


def test_wait_for_returns_the_first_answer_and_gives_up():
    answers = iter([None, None, "10.0.0.5"])
    assert bake.wait_for(lambda: next(answers), timeout=10, interval=0) == "10.0.0.5"
    assert bake.wait_for(lambda: None, timeout=0.01, interval=0) is None


def test_import_questions_counts_what_a_clean_build_asks(tmp_path):
    """A `--debug` build bakes its values in and asks nothing, with an identical
    action count — so this is the only thing that separates them."""
    clean = tmp_path / "clean.plist"
    clean.write_bytes(plistlib.dumps({"WFWorkflowImportQuestions": [{}, {}, {}]}))
    debug = tmp_path / "debug.plist"
    debug.write_bytes(plistlib.dumps({"WFWorkflowActions": [{}, {}]}))
    assert bake.import_questions(clean) == 3
    assert bake.import_questions(debug) == 0
