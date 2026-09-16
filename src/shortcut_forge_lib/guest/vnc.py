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

from urllib.parse import unquote, urlsplit

#: Apple's Screen Sharing port in the guest.
PORT = 5900


def server_arg(host: str, port: int = PORT) -> str:
    """`HOST::PORT`. The doubled colon is required; one colon means a display."""
    return f"{host}::{port}"


def auth_args(username: str, password: str) -> list[str]:
    """ARD authentication. Both halves, always — a missing username hangs."""
    return ["--username", username, "--password", password]


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
