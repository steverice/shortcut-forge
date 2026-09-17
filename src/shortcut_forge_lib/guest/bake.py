"""Bake a guest end to end, with nothing to click.

    create -> provision -> grant ARD privileges -> bless offline -> prove it

One command, no inputs but a name, and the result is a stopped guest that a VNC
client can see and drive. `bake()` always creates from the IPSW rather than
cloning: this is the path that rebuilds a base from nothing, and a clone of a
finished base is `tart clone`, which needs nothing from this module.

The order matters in one non-obvious way: the blessing is written while the
guest is **stopped**, between two boots. SIP refuses the write from inside a
running system, so there is no arrangement of steps that does it in one boot.

What "prove it" means here is the part worth keeping. Capture and input are
separate TCC grants, and a guest holding only the first renders a flawless
screenshot and ignores every click — so a color count alone will certify a base
that cannot be driven. The proof therefore clicks something and confirms the
effect **over SSH**, through a channel the framebuffer cannot fake. It also runs
after a restart, so it cannot mistake a momentary grant for a durable one.
"""

from __future__ import annotations

import os
import plistlib
import re
import secrets
import shutil
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from PIL import Image

from shortcut_forge_lib.guest import bless, ssh, tart, vnc

if TYPE_CHECKING:
    from collections.abc import Callable

    from shortcut_forge_lib.types import OnStep

T = TypeVar("T")

#: Free space for the IPSW, the restored guest, and room to work.
NEEDED_BYTES = 60 * 1024**3

#: Virtualization allows two concurrent macOS guests per host.
MAX_GUESTS = 2

#: Turning on Remote Management. Its access *mode* and its privilege *mask* are
#: separate, and setting the mask for a named user while the mode is "all users"
#: writes privileges to a record that mode never reads — a configured agent
#: permitted to do nothing. `-allUsers` with `-privs -all` sets both.
KICKSTART = "/System/Library/CoreServices/RemoteManagement/ARDAgent.app/Contents/Resources/kickstart"
KICKSTART_ARGS = "-activate -configure -allowAccessFor -allUsers -privs -all -restart -agent"

#: What the proof clicks, as a fraction of the framebuffer: Safari in the Dock,
#: measured at 1024x768. Held as a fraction so a different guest resolution
#: still lands on the Dock, and checked by name afterwards so a miss is reported
#: as a miss rather than as a missing grant.
DOCK_PROBE = (150 / 1024, 740 / 768)
PROBE_APP = "Safari"

#: Every GUI app's executable, one per line, deduplicated.
APP_PROCESSES = r"ps -Ao comm= | grep '\.app/Contents/MacOS/' | sed 's|.*/||' | sort -u"

#: A first boot restores the OS and walks Setup Assistant; later boots do not.
PROVISION_TIMEOUT = 1800.0
BOOT_TIMEOUT = 420.0
STOP_TIMEOUT = 180.0

#: After SSH answers, the window server and the Dock are still arriving.
DESKTOP_SETTLE = 20.0

#: Launching an app from the Dock, over VNC, on a guest sharing a host.
LAUNCH_SETTLE = 12.0

_VOLUME_NAME = re.compile(r"^\s*Volume Name:\s*(.+)$", re.MULTILINE)


class BakeError(RuntimeError):
    """The bake cannot proceed, or produced a guest that is not usable."""


@dataclass(frozen=True)
class Baked:
    """A guest and the credentials that reach it.

    `ip` is the address it answered on, which a later boot may not reuse; ask
    `tart ip` again rather than storing it. The name and the account are stable.
    """

    name: str
    ip: str
    username: str
    password: str


def new_password() -> str:
    """Fresh per bake: it is visible in `ps` while tart provisions the guest."""
    return secrets.token_urlsafe(18)


def missing_tools(tart_bin: str) -> list[str]:
    """Which of the four tools a bake needs are not on this host."""
    return [
        tool
        for tool in (tart_bin, "vncdo", "hdiutil", "/usr/libexec/PlistBuddy")
        if shutil.which(tool) is None and not Path(tool).exists()
    ]


def preconditions(
    name: str,
    *,
    running: int,
    existing: list[str],
    host_major: int,
    free_bytes: int,
    missing: list[str],
) -> list[str]:
    """Every reason this cannot work, gathered so one call names them all.

    Refusing up front matters more here than usual: the alternative is finding
    out twenty minutes into a restore.

    It reads nothing itself. Each fact is measured by the caller and passed in,
    so the refusals can be tested without the test's own host deciding the
    answer — which it did, twice, before this took its facts as arguments.
    """
    problems = []
    if host_major < 27:
        problems.append(f"host is macOS {host_major}; provisioning needs 27 or newer on BOTH host and guest")
    problems += [f"{tool} is not available" for tool in missing]
    if free_bytes < NEEDED_BYTES:
        problems.append(f"{free_bytes // 1024**3} GB free, need about {NEEDED_BYTES // 1024**3}")
    if name in existing:
        problems.append(f"a guest named {name} already exists")
    if running >= MAX_GUESTS:
        problems.append(f"{running} guests already running; Virtualization allows {MAX_GUESTS}")
    return problems


def bake(
    name: str,
    *,
    work_dir: Path,
    username: str = tart.DEFAULT_USERNAME,
    full_name: str = tart.DEFAULT_FULL_NAME,
    tart_bin: str = "tart",
    on_step: OnStep | None = None,
    on_credentials: Callable[[Baked], None] | None = None,
) -> Baked:
    """Create, bless, and prove a guest. Returns it stopped, ready to clone.

    `on_credentials` fires the moment SSH first answers, before anything that
    can fail. An earlier version saved them at the end instead, a later step
    refused the guest, and the password died with the run — leaving a guest
    nobody could log in to. A failed bake must still leave something reachable
    enough to diagnose.
    """

    def step(message: str) -> None:
        if on_step is not None:
            on_step(message)

    work_dir.mkdir(parents=True, exist_ok=True)
    known = _guests(tart_bin)
    refusals = preconditions(
        name,
        running=sum(state == "running" for state in known.values()),
        existing=list(known),
        host_major=host_major(),
        free_bytes=shutil.disk_usage("/").free,
        missing=missing_tools(tart_bin),
    )
    if refusals:
        raise BakeError("; ".join(refusals))

    password = new_password()
    provisioning = tart.Provisioning(full_name=full_name, username=username, password=password)

    step(f"1/6 create {name} from the IPSW (a download the first time)")
    _tart(tart_bin, tart.create_args(name), timeout=3600)

    step("2/6 first boot with provisioning — the only boot it applies to")
    host = _boot(
        name,
        work_dir=work_dir,
        tart_bin=tart_bin,
        username=username,
        password=password,
        provisioning=provisioning,
        timeout=PROVISION_TIMEOUT,
    )
    baked = Baked(name=name, ip=host, username=username, password=password)
    if on_credentials is not None:
        on_credentials(baked)
    step(f"    provisioned; SSH answers at {host}")

    step("3/6 turn on Remote Management, mode and privilege mask together")
    kick = ssh.run(
        host,
        f"{ssh.sudo(password, f'{KICKSTART} {KICKSTART_ARGS}')} 2>&1 | tail -3",
        user=username,
        password=password,
        work_dir=work_dir,
        timeout=300,
    )
    if kick.returncode:
        raise BakeError(f"kickstart failed: {kick.stdout.strip()} {kick.stderr.strip()}")

    step("4/6 stop, and write the blessing into the parked disk")
    _stop(name, tart_bin=tart_bin)
    write_blessing(tart.disk_image(name), work_dir, on_step=on_step)

    step("5/6 boot again — a grant that does not survive a restart is not a grant")
    host = _boot(
        name,
        work_dir=work_dir,
        tart_bin=tart_bin,
        username=username,
        password=password,
        timeout=BOOT_TIMEOUT,
    )
    time.sleep(DESKTOP_SETTLE)

    step("6/6 prove the screen renders and that a click lands")
    prove(host, username=username, password=password, work_dir=work_dir, on_step=on_step)

    _stop(name, tart_bin=tart_bin)
    step(f"baked: {name} is stopped and ready to clone")
    return Baked(name=name, ip=host, username=username, password=password)


def prove(
    host: str,
    *,
    username: str,
    password: str,
    work_dir: Path,
    on_step: OnStep | None = None,
) -> Path:
    """Both halves of the blessing, or a `BakeError` naming which half is missing.

    Returns the screenshot, which is worth keeping: when the click misses, it is
    the only record of where the Dock actually was.
    """

    def step(message: str) -> None:
        if on_step is not None:
            on_step(message)

    shot = work_dir / "proof.png"
    capture = subprocess.run(
        vnc.capture_args(host, username, password, str(shot)),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if capture.returncode or not shot.exists():
        raise BakeError(f"vncdo could not capture the screen: {capture.stderr.strip()}")
    colors = distinct_colors(shot)
    if not bless.rendered(colors):
        raise BakeError(
            f"the screen is a flat {colors}-color frame: kTCCServiceScreenCapture is not granted. "
            f"The capture itself succeeded, which is what a denied capture looks like. See {shot}."
        )
    step(f"    renders: {colors} distinct colors")

    def apps() -> set[str]:
        # One name per line, not per word: `Notification Center` is one app.
        out = ssh.run(host, APP_PROCESSES, user=username, password=password, work_dir=work_dir).stdout
        return {line.strip() for line in out.splitlines() if line.strip()}

    before = apps()
    if PROBE_APP in before:
        # Otherwise the proof is unfalsifiable: it would look for something that
        # is already there, see no change, and report a missing grant.
        ssh.run(host, f"osascript -e 'quit app \"{PROBE_APP}\"'", user=username, password=password, work_dir=work_dir)
        time.sleep(LAUNCH_SETTLE)
        before = apps()
    if PROBE_APP in before:
        raise BakeError(f"{PROBE_APP} is already running and will not quit; the proof cannot tell it apart")

    with Image.open(shot) as handle:
        width, height = handle.size
    x, y = probe_point(width, height)
    click = subprocess.run(
        vnc.click_args(host, username, password, x, y),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if click.returncode:
        raise BakeError(f"vncdo could not click: {click.stderr.strip()}")
    time.sleep(LAUNCH_SETTLE)
    started = apps() - before

    if PROBE_APP in started:
        step(f"    clicks land: {PROBE_APP} started, confirmed over SSH")
        return shot
    if started:
        raise BakeError(
            f"the click at ({x}, {y}) started {', '.join(sorted(started))} rather than {PROBE_APP}. "
            f"Input works; the Dock probe point is wrong for this guest. See {shot}."
        )
    raise BakeError(
        f"the click at ({x}, {y}) started nothing: kTCCServicePostEvent is not granted, "
        f"or the display is still at 2x, where every pointer event is dropped. See {shot}."
    )


def probe_point(width: int, height: int) -> tuple[int, int]:
    """`DOCK_PROBE` in pixels for a framebuffer of this size."""
    return int(DOCK_PROBE[0] * width), int(DOCK_PROBE[1] * height)


def host_major() -> int:
    version = subprocess.run(["sw_vers", "-productVersion"], capture_output=True, text=True, check=False).stdout
    return int(version.split(".")[0] or 0)


def volume_names(attach_output: str) -> list[tuple[str, str]]:
    """(device, volume name) for each device `hdiutil attach` reported.

    The names are not in the attach output, so each device is asked. Picking the
    Data volume by position instead would sometimes take the sealed system
    volume, which is read-only and fails confusingly.
    """
    pairs = []
    for line in attach_output.splitlines():
        parts = line.split()
        if not parts or not parts[0].startswith("/dev/disk"):
            continue
        info = subprocess.run(["diskutil", "info", parts[0]], capture_output=True, text=True, check=False).stdout
        match = _VOLUME_NAME.search(info)
        pairs.append((parts[0], match.group(1) if match else ""))
    return pairs


def distinct_colors(image: Path) -> int:
    """How many colors a capture holds. A denied one holds exactly one."""
    with Image.open(image) as handle:
        rgb = handle.convert("RGB")
        # Bound it above the pixel count: `getcolors` returns None past its
        # limit, which would read as a black screen on a busy desktop.
        colors = rgb.getcolors(maxcolors=rgb.width * rgb.height + 1)
    return len(colors or [])


def write_blessing(disk_image: Path, work_dir: Path, *, on_step: OnStep | None = None) -> None:
    """Write the TCC grants and the display scale into a **stopped** guest's disk.

    Raises rather than returning a status: a half-applied blessing produces a
    guest that looks fine and cannot be driven, which is the outcome this whole
    module exists to avoid.
    """

    def step(message: str) -> None:
        if on_step is not None:
            on_step(message)

    attach = subprocess.run(bless.attach_args(str(disk_image)), capture_output=True, text=True, check=False)
    if attach.returncode:
        raise BakeError(f"could not attach {disk_image}: {attach.stderr.strip()}")

    devices = volume_names(attach.stdout)
    container = devices[0][0] if devices else ""
    try:
        data = bless.data_volume(devices)
        if data is None:
            raise BakeError(f"no Data volume among {[d for d, _ in devices]}")
        mountpoint = work_dir / "guest-data"
        mountpoint.mkdir(parents=True, exist_ok=True)
        mounted = subprocess.run(bless.mount_args(data, str(mountpoint)), capture_output=True, text=True, check=False)
        if mounted.returncode:
            raise BakeError(f"could not mount {data}: {mounted.stderr.strip()}")
        try:
            write_rows(mountpoint / bless.TCC_DB)
            step("    TCC grants written")
            scaled = subprocess.run(
                bless.scale_args(str(mountpoint / bless.DISPLAYS_PLIST)),
                capture_output=True,
                text=True,
                check=False,
            )
            if scaled.returncode:
                raise BakeError(f"could not set the display scale: {scaled.stderr.strip()}")
            step("    display scale set to 1")
        finally:
            subprocess.run(["sync"], check=False)
            subprocess.run(["umount", str(mountpoint)], capture_output=True, check=False)
    finally:
        if container:
            subprocess.run(bless.detach_args(container), capture_output=True, check=False)


def write_rows(db: Path) -> None:
    """The two grants, into a TCC store nothing is currently using."""
    if not db.exists():
        raise BakeError(f"no TCC database at {db}")
    con = sqlite3.connect(db)
    try:
        con.executemany(bless.insert_sql(), [bless.row_values(row) for row in bless.tcc_rows()])
        con.commit()
        # Checkpoint, or the rows sit in the -wal and the guest boots without them.
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        con.close()


def import_questions(plist: Path) -> int:
    """How many setup questions a built shortcut carries.

    A clean build asks; a `--debug` build bakes its values in and asks nothing.
    Comparing this against what landed in a guest is the only way to see an
    import that silently lost its questions — the action count is unchanged.
    """
    with plist.open("rb") as handle:
        return len(plistlib.load(handle).get("WFWorkflowImportQuestions", []))


def wait_for(probe: Callable[[], T | None], *, timeout: float, interval: float = 8.0) -> T | None:
    """Poll until `probe` returns something, or the deadline passes."""
    deadline = time.monotonic() + timeout
    while True:
        found = probe()
        if found is not None:
            return found
        if time.monotonic() + interval >= deadline:
            return None
        time.sleep(interval)


def _tart(tart_bin: str, args: list[str], *, timeout: float = 120) -> str:
    done = subprocess.run([tart_bin, *args], capture_output=True, text=True, timeout=timeout, check=False)
    if done.returncode:
        raise BakeError(f"tart {' '.join(args)} failed: {done.stderr.strip()[-400:]}")
    return done.stdout


def _guests(tart_bin: str) -> dict[str, str]:
    """What tart knows about, or nothing when tart itself is missing.

    Swallowing that one error is deliberate: this runs before `preconditions`,
    whose whole job is to say `tart is not available` in plain words alongside
    every other reason. A raw `FileNotFoundError: 'tart'` from here would beat
    it to the exit and say less.
    """
    try:
        return tart.guests(_tart(tart_bin, tart.list_args()))
    except OSError:
        return {}


def _boot(
    name: str,
    *,
    work_dir: Path,
    tart_bin: str,
    username: str,
    password: str,
    timeout: float,
    provisioning: tart.Provisioning | None = None,
) -> str:
    """Start the guest and return the address SSH answered on.

    tart's output goes to a file, never a pipe: `tart run` does not exit and
    keeps writing, so a pipe nobody drains fills and wedges the guest, with
    "it never came up" as the only symptom.
    """
    log = work_dir / f"{name}-tart.log"
    with log.open("ab") as sink:
        proc = subprocess.Popen(
            [tart_bin, *tart.run_args(name, provisioning=provisioning)],
            stdout=sink,
            stderr=subprocess.STDOUT,
            env={**os.environ, **tart.HEADLESS_ENV},
        )

    def answering() -> str | None:
        if proc.poll() is not None:
            raise BakeError(f"tart run exited with {proc.returncode}; see {log}")
        found = subprocess.run(
            [tart_bin, *tart.ip_args(name)], capture_output=True, text=True, check=False
        ).stdout.strip()
        if not found:
            return None
        try:
            reachable = ssh.run(found, "echo up", user=username, password=password, work_dir=work_dir, timeout=20)
        except subprocess.TimeoutExpired:
            return None
        return found if reachable.returncode == 0 else None

    host = wait_for(answering, timeout=timeout)
    if host is None:
        raise BakeError(f"{name} never answered SSH within {timeout:.0f}s; see {log}")
    return host


def _stop(name: str, *, tart_bin: str) -> None:
    """Stop, then wait for tart to say so: the disk is not a file until it does."""
    _tart(tart_bin, tart.stop_args(name), timeout=STOP_TIMEOUT)

    def stopped() -> bool | None:
        return True if _guests(tart_bin).get(name) == "stopped" else None

    if wait_for(stopped, timeout=STOP_TIMEOUT, interval=3.0) is None:
        raise BakeError(f"{name} did not reach a stopped state; its disk cannot be written safely")
