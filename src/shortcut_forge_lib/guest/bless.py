"""Granting a guest's own screen to a VNC client, by writing its image while parked.

Scope: throwaway macOS guests created by `tart` on this host, so the automation
that drives a VM can see and click its screen. The end state is a normally
configured, SIP-enabled guest holding exactly the grants a person would have
made by hand in System Settings — the same destination by a different road.
Nothing here touches the host, and nothing disables SIP.

A freshly provisioned guest's Screen Sharing listens, authenticates, and serves
an entirely black frame. Nothing reports a permission problem: the client exits
0 with a well-formed image, and no prompt ever appears. Apple's own Screen
Sharing.app is refused too, with a message naming the remedy.

The cause is two rows missing from the system TCC store. The System Settings
flow writes them; `kickstart` does not, however its privilege mask is set.

They cannot be written from inside the running guest — SIP answers root with
`attempt to write a readonly database`. But SIP protects a *running* system, and
a stopped guest's disk is a file: mount its Data volume and write them there.
Mounting with `noowners` lets an ordinary user write root-owned files, so this
needs no host root either.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

#: The client the grants are for. Apple-signed, which is why `CSREQ` is portable.
SCREEN_SHARING_AGENT = "com.apple.screensharing.agent"

#: Capture and input are separate. A guest holding only the first renders
#: perfectly and silently drops every click, which no screenshot check can see.
GRANTED_SERVICES = ("kTCCServiceScreenCapture", "kTCCServicePostEvent")

#: The code requirement, extracted verbatim from a guest blessed through the UI.
#: It decodes to `identifier "com.apple.screensharing.agent" and anchor apple` —
#: identity and anchor, nothing hardware-bound — and the same bytes were shown to
#: work on a guest with a different ecid and MAC. So it is a constant here,
#: needing re-extraction only if Apple re-signs the agent.
#:
#: That portability is a property of the client being Apple-signed, NOT of the
#: technique. A third-party client's requirement pins to the vendor's signature
#: and changes with every vendor build. Never fabricate one: a wrong or absent
#: csreq produces a row that dumps as perfectly correct and is ignored at
#: authorization time — the same silent-success shape as everything else here.
CSREQ = bytes.fromhex(
    "FADE0C000000003C0000000100000006000000020000001D636F6D2E6170706C652E"
    "73637265656E73686172696E672E6167656E7400000000000003"
)

#: Paths relative to the mounted Data volume.
TCC_DB = "Library/Application Support/com.apple.TCC/TCC.db"
DISPLAYS_PLIST = "Library/Preferences/com.apple.windowserver.displays.plist"

#: A guest boots at 2x HiDPI, where it renders flawlessly and drops every
#: pointer event while keyboard events still land. 1x is drivable.
SCALE_KEY = ":DisplayAnyUserSets:Configs:0:DisplayConfig:0:CurrentInfo:Scale"

_COLUMNS = (
    "service",
    "client",
    "client_type",
    "auth_value",
    "auth_reason",
    "auth_version",
    "csreq",
    "policy_id",
    "indirect_object_identifier_type",
    "indirect_object_identifier",
    "indirect_object_code_identity",
    "flags",
    "pid",
    "pid_version",
    "one_time_reprompt_eligible",
    "reminder_count",
)


def tcc_rows(csreq: bytes = CSREQ) -> list[dict[str, object]]:
    """The two rows to write, column-keyed. `auth_value` 2 is "allowed"."""
    return [
        {
            "service": service,
            "client": SCREEN_SHARING_AGENT,
            "client_type": 0,
            "auth_value": 2,
            "auth_reason": 4,
            "auth_version": 1,
            "csreq": csreq,
            "policy_id": None,
            "indirect_object_identifier_type": None,
            "indirect_object_identifier": "UNUSED",
            "indirect_object_code_identity": None,
            "flags": 0,
            "pid": None,
            "pid_version": None,
            "one_time_reprompt_eligible": 0,
            "reminder_count": 0,
        }
        for service in GRANTED_SERVICES
    ]


def insert_sql() -> str:
    """Every column named, so a partial row cannot be written."""
    placeholders = ",".join("?" * len(_COLUMNS))
    return f"INSERT OR REPLACE INTO access ({','.join(_COLUMNS)}) VALUES ({placeholders})"  # noqa: S608 - fixed column list


def row_values(row: dict[str, object]) -> tuple[object, ...]:
    """A row's values in `insert_sql()`'s column order."""
    return tuple(row[column] for column in _COLUMNS)


def attach_args(disk_image: str) -> list[str]:
    """Attach without mounting; the Data volume is mounted deliberately after."""
    return ["hdiutil", "attach", "-nomount", disk_image]


def detach_args(device: str) -> list[str]:
    return ["hdiutil", "detach", device]


def mount_args(device: str, mountpoint: str) -> list[str]:
    """`noowners` is what removes the need for host root.

    It lets an ordinary user write root-owned files inside the mounted image. If
    a host policy ever forbids the flag, `sudo mount_apfs` is the fallback —
    still headless, but needing a cached credential.
    """
    return ["mount", "-t", "apfs", "-o", "noowners", device, mountpoint]


def scale_args(plist_path: str) -> list[str]:
    """Set the guest's display scale to 1, so pointer events land."""
    return ["/usr/libexec/PlistBuddy", "-c", f"Set {SCALE_KEY} 1", plist_path]


def data_volume(devices: Iterable[tuple[str, str]]) -> str | None:
    """Pick the Data volume from (device, volume name) pairs.

    By name, not by position: an attached container also exposes ISC, Recovery
    and the sealed system volume, and their order is not guaranteed.
    """
    for device, name in devices:
        if name.strip() == "Data":
            return device
    return None


def rendered(distinct_colors: int) -> bool:
    """Whether a captured frame shows anything at all.

    A denied capture is a well-formed image of exactly one colour, so this is
    what distinguishes it from a working screen. It says nothing about whether
    clicks land: capture and input are separate grants, and proving input needs
    a real click confirmed out of band.
    """
    return distinct_colors > 2
