"""Reaching a provisioned guest over SSH.

The provisioned password goes into a 0700 helper for the length of one call
rather than onto an argv, where `ps` would show it to any local account. It
still reaches `ps` once, on tart's own `--provisioning-opts`, which is tart's
interface and not something a caller can avoid — hence a fresh random password
per bake rather than a reused constant.

`known_hosts` is per-call and disposable. Every fresh guest brings a new host
key on a recycled DHCP address, so writing to the real `~/.ssh/known_hosts`
would make the second guest fail verification against the first one's key.
"""

from __future__ import annotations

import contextlib
import os
import shlex
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@contextlib.contextmanager
def askpass(work_dir: Path, password: str) -> Iterator[dict[str, str]]:
    """An environment in which `ssh` can read the password, and nothing else can."""
    helper = work_dir / "askpass.sh"
    helper.write_text(f"#!/bin/sh\necho {shlex.quote(password)}\n", encoding="utf-8")
    helper.chmod(0o700)
    try:
        yield {
            **os.environ,
            "SSH_ASKPASS": str(helper),
            "SSH_ASKPASS_REQUIRE": "force",
            "DISPLAY": "none",
        }
    finally:
        helper.unlink(missing_ok=True)


def ssh_args(host: str, user: str, known_hosts: Path) -> list[str]:
    return [
        "/usr/bin/ssh",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"UserKnownHostsFile={known_hosts}",
        "-o",
        "ConnectTimeout=10",
        f"{user}@{host}",
        "bash",
        "-s",
    ]


def run(
    host: str,
    script: str,
    *,
    user: str,
    password: str,
    work_dir: Path,
    timeout: float = 120,
) -> subprocess.CompletedProcess[str]:
    """One round trip. The script arrives on stdin, so quoting is the guest's problem.

    Never background a long-lived process in `script` without redirecting its
    output: it holds the channel open and the call times out rather than
    returning.
    """
    known_hosts = work_dir / "known_hosts"
    with askpass(work_dir, password) as env:
        return subprocess.run(
            ssh_args(host, user, known_hosts),
            input=script,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )


def sudo(password: str, command: str) -> str:
    """`sudo -S` reading the password from stdin, for use inside a script."""
    return f"echo {shlex.quote(password)} | sudo -S {command}"
