"""Action UUIDs, minted two ways.

The validator hard-rejects repeating-hex placeholders, so UUIDs have to look
random. Whether they have to *be* random is a project decision: fresh UUIDs on
every build make the built file churn completely, which is fine when `dist/`
is not committed; stable UUIDs make a committed build a readable diff.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator


def random_uuids() -> Iterator[str]:
    """An endless supply of fresh uppercase UUIDs. Every build differs."""
    while True:
        yield str(uuid.uuid4()).upper()


class RoleUuids:
    """Stable uppercase UUIDs, one per role name, under a fixed namespace.

    `RoleUuids(ns)("weather")` is the same string on every build, so a
    committed XML changes only where the shortcut did. Two shortcuts built
    from the same namespace must not share role names, or their actions will
    share UUIDs; prefix the role (`"wrapper-run"`) instead.
    """

    def __init__(self, namespace: uuid.UUID | str) -> None:
        self.namespace = namespace if isinstance(namespace, uuid.UUID) else uuid.UUID(namespace)

    def __call__(self, role: str) -> str:
        return str(uuid.uuid5(self.namespace, role)).upper()

    def many(self, roles: list[str] | tuple[str, ...]) -> dict[str, str]:
        """A role-to-UUID mapping for a whole list of roles at once."""
        return {role: self(role) for role in roles}
