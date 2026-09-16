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

import os
from dataclasses import dataclass
from pathlib import Path

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
    return ["list"]


def guests(listing: str) -> dict[str, str]:
    """Guest name to state, from `tart list`.

    Parsed by position from each end rather than by column offset: the
    `Accessed` column holds free text like `55 minutes ago`, so the number of
    fields varies and only the first two and the last are fixed.
    """
    states = {}
    for line in listing.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 3:
            states[parts[1]] = parts[-1]
    return states


def home() -> Path:
    """Where tart keeps its VMs. `TART_HOME` overrides it, as tart itself honors."""
    return Path(os.environ.get("TART_HOME") or Path.home() / ".tart")


def disk_image(name: str) -> Path:
    """The guest's disk, which is an ordinary file whenever the guest is stopped."""
    return home() / "vms" / name / "disk.img"
