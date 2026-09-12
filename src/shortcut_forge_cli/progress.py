"""The one console every command prints through."""

from __future__ import annotations

from rich.console import Console

_console = Console()


def info(msg: str) -> None:
    _console.print(msg)


def success(msg: str) -> None:
    _console.print(f"[green]✓[/green] {msg}")


def error(msg: str) -> None:
    _console.print(f"[red]✗[/red] {msg}")


def warning(msg: str) -> None:
    _console.print(f"[yellow]⚠[/yellow] {msg}")
