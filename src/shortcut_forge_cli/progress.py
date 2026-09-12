"""The one console every command prints through."""

from __future__ import annotations

from rich.console import Console

# soft_wrap: when the output is a file or a pipe Rich would otherwise wrap every
# line at 80 columns, which splits a build log's lines in the middle of a path.
_console = Console(soft_wrap=True)


def info(msg: str) -> None:
    _console.print(msg)


def success(msg: str) -> None:
    _console.print(f"[green]✓[/green] {msg}")


def error(msg: str) -> None:
    _console.print(f"[red]✗[/red] {msg}")


def warning(msg: str) -> None:
    _console.print(f"[yellow]⚠[/yellow] {msg}")
