"""Talking to a guest's Screen Sharing with `vncdotool`.

Two things here were paid for by failing runs.

Screen Sharing speaks Apple's ARD authentication, so a **username** is required
alongside the password. Given only `--password`, vncdotool falls back to an
interactive `getpass` prompt and hangs until it is killed — a silent stall, not
an error. `kickstart -setvnclegacy` does not help: the legacy VNC password path
makes it prompt for the username just the same.

And `--server` cannot take the `vnc://` URL tart prints. It wants
`ADDRESS::PORT`, with **two** colons for a port (one colon means a display
number).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Apple's Screen Sharing port in the guest.
PORT = 5900

#: What replaces the password when an argv reaches a human or a log.
REDACTED = "<password>"


def server_arg(host: str, port: int = PORT) -> str:
    """`HOST::PORT`. The doubled colon is required; one colon means a display."""
    return f"{host}::{port}"


def auth_args(username: str, password: str) -> list[str]:
    """ARD authentication. Both halves, always — a missing username hangs."""
    return ["--username", username, "--password", password]


def redacted(argv: Sequence[str]) -> list[str]:
    """`argv` with the value after `--password` replaced, for anything a human will read.

    `subprocess.TimeoutExpired` and `CalledProcessError` both render `cmd` in
    their `str()`, so a vncdo argv that reaches a traceback, a log or an error
    message carries the guest's password with it — and `check=False` does not
    help, because a timeout raises regardless. vncdotool reads the password
    only from argv, with no file or environment alternative, so unlike
    `ssh.run` — which keeps it in a 0700 askpass helper — this cannot avoid
    putting it there. Redacting on the way out is what is left.

    It also reaches `ps` for the length of the call, the same exposure
    `ssh.run`'s docstring describes for tart's `--provisioning-opts`, and for
    the same reason it is survivable: a bake mints a fresh random password
    rather than reusing a constant.
    """
    out = list(argv)
    for i, arg in enumerate(out[:-1]):
        if arg == "--password":
            out[i + 1] = REDACTED
    return out


def parse_url(url: str) -> tuple[str, int, str]:
    """Split a `vnc://:password@host:port` URL into (host, port, password).

    `--vnc-experimental` prints one of these, pointing at loopback on the host.
    `--vnc` prints `vnc://<guest-ip>/` with no port or password, since the guest
    is serving it. Kept because the URL shape is easy to misread as usable by a
    client directly, and it is not.
    """
    parts = urlsplit(url)
    return parts.hostname or "", parts.port or PORT, unquote(parts.password or "")


def capture_args(host: str, username: str, password: str, out: str) -> list[str]:
    return ["vncdo", "--server", server_arg(host), *auth_args(username, password), "capture", out]


def click_args(host: str, username: str, password: str, x: int, y: int) -> list[str]:
    """Move, settle, then click. A click without the move lands where the
    pointer already was."""
    return [
        "vncdo",
        "--server",
        server_arg(host),
        *auth_args(username, password),
        "move",
        str(x),
        str(y),
        "pause",
        "1",
        "click",
        "1",
    ]
