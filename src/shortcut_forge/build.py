"""Write, check, validate, and sign a set of shortcuts in one call.

A generator builds its documents and hands them to `build_all()`. What comes
back is the signed file for each, ready to import. Everything that can stop
the build stops it before anything is signed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from shortcut_forge import checks, toolchain
from shortcut_forge.plist import write_xml

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Sequence
    from pathlib import Path

    from shortcut_forge.types import OnStep


@dataclass
class Shortcut:
    """One shortcut to build.

    `name` is the name it will have in the library. The signed file is named
    after it, because an imported shortcut takes its file's name, not the
    `WFWorkflowName` inside. `xml_stem` names the unsigned XML when a project
    wants something other than the shortcut's name on disk, for instance a
    kebab-case committed file.
    """

    name: str
    document: dict[str, Any]
    xml_stem: str | None = None

    @property
    def xml_name(self) -> str:
        return f"{self.xml_stem or self.name}.xml"


def build_all(
    dist: Path,
    shortcuts: Sequence[Shortcut],
    *,
    waived: Iterable[str] = (),
    mode: str = "anyone",
    sign: bool = True,
    run_checks: bool = True,
    known_shortcuts: Collection[str] | None = None,
    target_macos: str = toolchain.DEFAULT_TARGET_MACOS,
    platform: str = toolchain.DEFAULT_PLATFORM,
    on_step: OnStep | None = None,
) -> dict[str, Path]:
    """Build every shortcut into `dist`. Returns each name's signed file, or its XML when not signing.

    Order of operations: every document is checked and written first, then
    every file is validated, and only then is anything signed. A structural
    or validation failure therefore leaves nothing half-signed.

    `known_shortcuts` defaults to the names being built, so a wrapper that
    runs another shortcut in the same build is checked against it. Pass a
    larger set when a shortcut runs something built elsewhere.
    """
    dist.mkdir(parents=True, exist_ok=True)
    names = [s.name for s in shortcuts]
    known = known_shortcuts if known_shortcuts is not None else names
    waivers = list(waived)

    def step(message: str) -> None:
        if on_step is not None:
            on_step(message)

    written: dict[str, Path] = {}
    for shortcut in shortcuts:
        if run_checks:
            checks.check_all(shortcut.document, known_shortcuts=known)
        written[shortcut.name] = write_xml(shortcut.document, dist / shortcut.xml_name)
        step(f"{shortcut.name}: wrote {written[shortcut.name].name}")

    for shortcut in shortcuts:
        report = toolchain.validate(
            written[shortcut.name], waived=waivers, target_macos=target_macos, platform=platform
        )
        if not report.ok:
            raise toolchain.ValidationError(report.xml, report.unexpected)
        step(f"{shortcut.name}: validated")

    if not sign:
        return written

    signed: dict[str, Path] = {}
    for shortcut in shortcuts:
        signed[shortcut.name] = toolchain.sign(written[shortcut.name], name=shortcut.name, mode=mode)
        step(f"{shortcut.name}: signed -> {signed[shortcut.name]}")
    return signed
