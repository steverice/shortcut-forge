"""The validator and the signer, which are the shortcuts-playground plugin's.

`validate-shortcut` checks a plist against the plugin's catalog and prints
one `- ` line per problem. `sign-shortcut` runs Apple's `shortcuts sign`,
which is what turns an XML plist into a `.shortcut` file a phone will import.
Both are found on `PATH`; `SHORTCUT_FORGE_VALIDATOR` and
`SHORTCUT_FORGE_SIGNER` override the lookup.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

DEFAULT_TARGET_MACOS = "27"
DEFAULT_PLATFORM = "ios"


class ToolNotFoundError(RuntimeError):
    """A required command-line tool is not on PATH."""


class ValidationError(RuntimeError):
    """The validator reported something no waiver covers."""

    def __init__(self, xml: Path, unexpected: list[str]) -> None:
        super().__init__(f"{xml.name}: " + "; ".join(unexpected))
        self.xml = xml
        self.unexpected = unexpected


class SigningError(RuntimeError):
    """The signer failed on every attempt."""


@dataclass
class ValidationReport:
    """What the validator said about one file."""

    xml: Path
    status: int
    report: str
    errors: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unexpected


def find_tool(name: str, env_var: str) -> Path:
    """Locate a tool by environment override, then by PATH."""
    override = os.environ.get(env_var)
    if override:
        return Path(override)
    found = shutil.which(name)
    if found is None:
        raise ToolNotFoundError(f"{name} is not on PATH; install the shortcuts-playground plugin or set {env_var}")
    return Path(found)


def validate(
    xml: Path,
    *,
    waived: Iterable[str] = (),
    target_macos: str = DEFAULT_TARGET_MACOS,
    platform: str = DEFAULT_PLATFORM,
) -> ValidationReport:
    """Run the validator and sort its complaints into waived and unexpected.

    `waived` is a list of regular expressions. An error line matching any of
    them is expected and deliberate; the caller's comments should say why.
    Waive by name, never wholesale: this filter has caught real regressions,
    including a malformed control-flow block and a genuinely empty parameter.

    A non-zero exit with nothing itemized is not a waivable rule. It is the
    validator saying it never got as far as the rules (an unreadable plist,
    for one), and it counts as unexpected.
    """
    tool = find_tool("validate-shortcut", "SHORTCUT_FORGE_VALIDATOR")
    result = subprocess.run(
        [str(tool), str(xml), "--target-macos", target_macos, "--target-platform", platform],
        capture_output=True,
        text=True,
        check=False,
    )
    report = result.stdout + result.stderr
    errors = [line for line in report.splitlines() if line.startswith("- ")]
    patterns = [re.compile(w) for w in waived]
    unexpected = [e for e in errors if not any(p.search(e) for p in patterns)]
    if result.returncode != 0 and not errors:
        unexpected.append(f"validate-shortcut exited {result.returncode} without itemizing anything: {report.strip()}")
    return ValidationReport(xml=xml, status=result.returncode, report=report, errors=errors, unexpected=unexpected)


def sign(
    xml: Path,
    *,
    name: str,
    mode: str = "anyone",
    output_dir: Path | None = None,
    attempts: int = 2,
) -> Path:
    """Sign an XML plist and return the path of the `.shortcut` it produced.

    `mode="anyone"` is what makes the file shareable: Apple signs it on its
    server and anybody can import it. `people-who-know-me` embeds your
    contact card and works only for people who already have you in Contacts.
    The signer's own default reads from the environment, so it is pinned
    here rather than left to a variable nobody remembers setting.

    `output_dir` defaults to the directory the XML is in, so the signed file
    and the signer's dated XML archive land next to the build rather than in
    the signer's global output folder. A debug build with credentials baked
    in then stays inside the build directory it was written to.

    Signing is intermittently flaky ("Failed to modify some records", or a
    500), so it is tried `attempts` times before giving up.
    """
    tool = find_tool("sign-shortcut", "SHORTCUT_FORGE_SIGNER")
    out_dir = output_dir if output_dir is not None else xml.parent
    argv = [str(tool), str(xml), "--name", name, "--mode", mode, "--output-dir", str(out_dir)]
    last = ""
    for _ in range(attempts):
        result = subprocess.run(argv, capture_output=True, text=True, check=False)
        if result.returncode == 0:
            signed = out_dir / f"{name}.shortcut"
            if not signed.exists():
                raise SigningError(f"sign-shortcut exited 0 but {signed} does not exist")
            return signed
        last = (result.stdout + result.stderr).strip()
    raise SigningError(f"sign-shortcut failed {attempts} times on {xml.name}: {last}")
