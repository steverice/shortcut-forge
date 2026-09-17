"""The guest module's pure parts.

Every assertion here is a finding that cost something to establish, written as a
test so a later edit cannot quietly undo it. Nothing boots a VM. The measurements
behind each one are in `docs/macos-guest.md`.
"""

from __future__ import annotations

import json
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


#: What `tart run` wrote when it was issued the instant `tart create` returned,
#: captured verbatim. The restore had not released the guest yet.
LOCKED_OUTPUT = (
    'Error Domain=VZErrorDomain Code=2 "Failed to lock auxiliary storage." '
    "UserInfo={NSLocalizedFailure=Invalid virtual machine configuration., "
    "NSLocalizedFailureReason=Failed to lock auxiliary storage., "
    'NSUnderlyingError=0x7bfca18150 {Error Domain=NSPOSIXErrorDomain Code=35 "Resource temporarily unavailable"}}'
)


def test_turning_on_sharing_does_both_halves():
    """Kickstart activates Remote Management and leaves nothing on 5900. The
    screensharing daemon is a separate service and is what a client connects to,
    so a script with only the first half yields `Connection refused` four steps
    later, long after the step that was actually incomplete reported success."""
    script = bake.sharing_script("pw")
    assert bake.KICKSTART in script
    assert f"launchctl kickstart -k {bake.SCREEN_SHARING_JOB}" in script


def test_the_lock_message_matches_what_tart_actually_prints():
    """The boot after a create races the restore's own hold on the guest, and
    this string is how the loser is told apart from a real failure. A wrong one
    turns a retryable race into a failed bake twenty minutes in."""
    assert bake.LOCKED in LOCKED_OUTPUT


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


# -- finding a guest again ---------------------------------------------------


def test_credentials_round_trip(tmp_path):
    made = bake.Baked(name="g", ip="10.0.0.5", username="probe", password="hunter2")
    written = bake.save_credentials(made, directory=tmp_path)
    assert written.stat().st_mode & 0o777 == 0o600, "a password file is readable only by its owner"
    found = bake.credentials("g", directory=tmp_path)
    assert (found.name, found.username, found.password) == ("g", "probe", "hunter2")


def test_credentials_does_not_hand_back_a_stored_address(tmp_path):
    """DHCP gives the guest a fresh address per boot, so a stored one is a stale
    one. It comes back empty to force the caller to ask `tart ip` instead."""
    bake.save_credentials(bake.Baked(name="g", ip="10.0.0.5", username="probe", password="pw"), directory=tmp_path)
    assert bake.credentials("g", directory=tmp_path).ip == ""


def test_a_missing_record_is_nameable_and_says_what_to_do(tmp_path):
    """Callers need to distinguish "never baked" from a corrupt file, and the
    remedy is the same either way: the password exists nowhere else."""
    with pytest.raises(bake.NoCredentialsError, match="re-bake"):
        bake.credentials("never-baked", directory=tmp_path)


def test_an_unreadable_record_is_the_same_named_error(tmp_path):
    (tmp_path / "g.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(bake.NoCredentialsError, match="unreadable"):
        bake.credentials("g", directory=tmp_path)


def test_credentials_default_is_outside_any_repo():
    """`build/` is gitignored but `git clean -xdf` erases it, and a worktree
    gives a second copy to disagree with."""
    assert "build" not in bake.CREDENTIALS_DIR.parts
    assert bake.CREDENTIALS_DIR.is_absolute()


# -- telling a copy from a lookalike -----------------------------------------


def _guest_dir(root, name, ecid, mac="00:11:22:33:44:55"):
    d = root / "vms" / name
    d.mkdir(parents=True)
    (d / "config.json").write_text(json.dumps({"ecid": ecid, "macAddress": mac}), encoding="utf-8")
    return d


def test_a_clone_is_found_by_identity_not_by_name(tmp_path, monkeypatch):
    """Measured: `tart clone` copies `ecid` verbatim and regenerates the MAC, so
    identity is what says two guests are the same VM. Names cannot: a clone left
    running by a crashed mint carries a name the next run never chose, and that
    leaked clone is exactly the concurrent copy Apple re-derives an identity for."""
    monkeypatch.setenv("TART_HOME", str(tmp_path))
    _guest_dir(tmp_path, "base", "ECID-A", mac="66:e2:e9:c7:9d:a7")
    _guest_dir(tmp_path, "left-over-from-a-crash", "ECID-A", mac="be:d9:ce:a3:a2:78")
    _guest_dir(tmp_path, "someone-elses-guest", "ECID-B")
    found = tart.copies_of(tart.machine_id("base"), ["left-over-from-a-crash", "someone-elses-guest"])
    assert found == ["left-over-from-a-crash"]


def test_an_unrelated_guest_does_not_block(tmp_path, monkeypatch):
    """Refusing on any running guest would block a mint whenever anything else
    is on the host, which is not the rule Apple states."""
    monkeypatch.setenv("TART_HOME", str(tmp_path))
    _guest_dir(tmp_path, "base", "ECID-A")
    _guest_dir(tmp_path, "unrelated", "ECID-B")
    assert tart.copies_of(tart.machine_id("base"), ["unrelated"]) == []


def test_the_base_running_is_itself_a_conflict(tmp_path, monkeypatch):
    """A clone must not start while its base runs, so a caller passing the base
    among the running guests should see it matched rather than filtered out."""
    monkeypatch.setenv("TART_HOME", str(tmp_path))
    _guest_dir(tmp_path, "base", "ECID-A")
    assert tart.copies_of(tart.machine_id("base"), ["base"]) == ["base"]


def test_an_unknown_identity_cannot_be_stepped_over(tmp_path, monkeypatch):
    """The precondition lives in the signature. An earlier version took a name
    and resolved it internally, so an unreadable base config yielded an empty
    list — a check answering "no conflicts" without having looked, which is the
    silent-success shape `docs/macos-guest.md` is largely about."""
    monkeypatch.setenv("TART_HOME", str(tmp_path))
    assert tart.machine_id("base-that-is-not-there") is None
    # `copies_of` takes the identity, so that None has to be handled before the
    # question can be asked at all.
    import inspect

    assert list(inspect.signature(tart.copies_of).parameters) == ["machine", "among"]


def test_an_unreadable_guest_is_left_out_not_guessed(tmp_path, monkeypatch):
    """The permissive direction, deliberately: a half-deleted VM directory should
    not block a release. A caller for whom a false pass costs more should treat
    an unreadable guest as a conflict itself."""
    monkeypatch.setenv("TART_HOME", str(tmp_path))
    _guest_dir(tmp_path, "base", "ECID-A")
    (tmp_path / "vms" / "broken").mkdir(parents=True)
    (tmp_path / "vms" / "broken" / "config.json").write_text("{not json", encoding="utf-8")
    assert tart.machine_id("broken") is None
    assert tart.machine_id("never-created") is None
    assert tart.copies_of(tart.machine_id("base"), ["broken", "never-created"]) == []
