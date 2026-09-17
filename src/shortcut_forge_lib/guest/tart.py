"""Argument construction for the `tart` CLI.

Only argv shapes live here, so they can be tested without a VM. The flag choices
are not preferences; both were established by breaking a guest:

- **`--vnc`, never `--vnc-experimental`.** The experimental server renders, but
  it is Virtualization's private VNC path and it took a guest down twice with
  SIGTRAP inside `-[_VZVirtualMachineAccessor addAccessorObserver:]`, within a
  couple of minutes of GUI activity each time. `--vnc` uses the guest's own
  Screen Sharing, which is not private API.
- **`CI=true` in the environment, never `--no-graphics`.** Both suppress tart's
  host-side auto-open of a VNC client, but `--no-graphics` removes the display
  *device*: no WindowServer, GUI apps cannot launch, and the `shortcuts` CLI
  fails with "Couldn't communicate with a helper application". The pairing
  `--no-graphics --vnc-experimental` looks coherent only because the
  experimental server supplies a display of its own and hides the other's
  effect.

`--provisioning-opts` wraps `VZMacGuestProvisioningOptions` and needs macOS 27
or newer on **both** host and guest — an absolute floor, not a relative rule.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

#: tart's help: the flag exists from 2.35.0 but is compiled only under the
#: Xcode 27 toolchain, so an official binary at or above this is the floor.
MIN_TART = "2.37.0"

#: The environment that suppresses tart's host-side auto-open without costing
#: the guest its display.
HEADLESS_ENV = {"CI": "true"}

#: The account a bake provisions. Not a person's account — a guest is a
#: throwaway, and its password is fresh per bake.
DEFAULT_USERNAME = "probe"
DEFAULT_FULL_NAME = "Guest Probe"


@dataclass(frozen=True)
class Provisioning:
    """First-boot account setup. Applies only to the first boot after creation.

    The password reaches tart's argv and is therefore visible in `ps` on the
    host for the life of the run. Generate a fresh one per bake rather than
    reusing a constant; this is tart's interface and a caller cannot avoid it.
    """

    full_name: str
    username: str
    password: str
    logs_in_automatically: bool = True
    enables_remote_login: bool = True


def provisioning_opts(provisioning: Provisioning) -> str:
    """One comma-separated `key=value` list — not JSON, not repeated flags.

    Booleans must be the literal strings `true`/`false`; anything else throws.
    """

    def flag(value: bool) -> str:
        return "true" if value else "false"

    return ",".join(
        [
            f"fullName={provisioning.full_name}",
            f"username={provisioning.username}",
            f"password={provisioning.password}",
            f"logsInAutomatically={flag(provisioning.logs_in_automatically)}",
            f"enablesRemoteLogin={flag(provisioning.enables_remote_login)}",
        ]
    )


def create_args(name: str, *, ipsw: str = "latest") -> list[str]:
    """`latest` downloads and caches the IPSW, so `ipsw` is not a required tool."""
    return ["create", f"--from-ipsw={ipsw}", name]


def clone_args(source: str, destination: str) -> list[str]:
    """Copy-on-write: a clone costs almost nothing until the guest writes.

    The MAC is regenerated on collision but the `VZMacMachineIdentifier` is not,
    so a clone and its source must never run at the same time.
    """
    return ["clone", source, destination]


def run_args(
    name: str,
    *,
    dir_shares: dict[str, str] | None = None,
    provisioning: Provisioning | None = None,
) -> list[str]:
    """Run headless with the guest's own Screen Sharing.

    Pair with `HEADLESS_ENV`; see the module docstring for why `--no-graphics`
    is not used and is not an option here.
    """
    args = ["run", name, "--vnc"]
    for label, path in (dir_shares or {}).items():
        args.append(f"--dir={label}:{path}")
    if provisioning is not None:
        args += ["--provisioning-opts", provisioning_opts(provisioning)]
    return args


def stop_args(name: str) -> list[str]:
    return ["stop", name]


def ip_args(name: str) -> list[str]:
    return ["ip", name]


def delete_args(name: str) -> list[str]:
    return ["delete", name]


def list_args() -> list[str]:
    """JSON, because the text table cannot be parsed by column.

    Its `Accessed` column holds free text — `55 minutes ago` — so the field
    count varies per row and a positional parser reads the wrong thing on some
    of them. The JSON gives `Name` and `State` as keys.
    """
    return ["list", "--format", "json"]


def guests(listing: str) -> dict[str, str]:
    """Guest name to state, from `tart list --format json`."""
    return {row["Name"]: row["State"] for row in json.loads(listing or "[]")}


def home() -> Path:
    """Where tart keeps its VMs. `TART_HOME` overrides it, as tart itself honors."""
    return Path(os.environ.get("TART_HOME") or Path.home() / ".tart")


def disk_image(name: str) -> Path:
    """The guest's disk, which is an ordinary file whenever the guest is stopped."""
    return home() / "vms" / name / "disk.img"


def config_path(name: str) -> Path:
    """The guest's configuration, readable without booting it."""
    return home() / "vms" / name / "config.json"


def machine_id(name: str) -> str | None:
    """The `ecid` Apple keys a VM's identity on, or None if it cannot be read.

    `tart clone` copies this verbatim while regenerating the MAC — measured on
    2026-09-17, cloning a base and diffing the two configs. That is what makes
    two guests *copies of each other* rather than merely similar, and Apple's
    "Using iCloud with macOS Virtual Machines" turns on exactly that distinction:
    a second copy started while another runs gets a new identity, and whoever
    signed in has to reauthenticate by hand.

    Unmeasured, and assumed: that two independently created guests never collide
    here. `tart create` mints one per VM, so a collision would be surprising.
    """
    try:
        return json.loads(config_path(name).read_text(encoding="utf-8")).get("ecid")
    except (OSError, json.JSONDecodeError):
        return None


def copies_of(machine: str, among: Iterable[str]) -> list[str]:
    """Which of `among` are copies of the VM with this identity.

    For refusing to start a second copy while one runs. Compare identity rather
    than names: a clone left behind by a crashed run carries a name this process
    never chose, and that leaked clone is both the likeliest concurrent copy and
    the exact case the rule covers.

    It takes the **identity**, not a name, so that a caller cannot ask this
    question without first establishing the identity it is asking about. An
    earlier version took a name and looked it up here, which meant an unreadable
    base config produced an empty list — a check answering "no conflicts"
    without having looked, which is the shape most of `docs/macos-guest.md` is
    about. `machine_id()` returns None there, and the None is now impossible to
    step over on the way in.

    A guest in `among` whose own identity cannot be read is left out rather than
    guessed at. That direction is deliberate and it is the permissive one: a
    half-deleted VM directory should not block a release. A caller for whom a
    false pass costs more than a false refusal — which is true of anything that
    has already cloned and imported before it finds out — should treat an
    unreadable guest as a conflict itself.
    """
    return [other for other in among if machine_id(other) == machine]
