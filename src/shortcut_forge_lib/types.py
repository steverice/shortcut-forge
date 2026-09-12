"""Types shared across the library."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeAlias

OnStep: TypeAlias = Callable[[str], None]
"""A progress callback. The library reports steps through it; the CLI draws them."""
