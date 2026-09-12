from __future__ import annotations

import itertools

from shortcut_forge.uuids import RoleUuids, random_uuids

NAMESPACE = "6E679556-DF2D-4820-B246-FD8F48BA3355"


def test_random_uuids_are_endless_uppercase_and_distinct():
    batch = list(itertools.islice(random_uuids(), 300))
    assert len(set(batch)) == 300
    assert all(u == u.upper() and len(u) == 36 for u in batch)


def test_role_uuids_are_stable_uppercase_and_role_specific():
    u = RoleUuids(NAMESPACE)
    assert u("weather") == u("weather")
    assert u("weather") == RoleUuids(NAMESPACE)("weather")
    assert u("weather") != u("date")
    assert u("weather") == u("weather").upper()
    assert len(u("weather")) == 36


def test_role_uuids_match_the_generator_that_committed_them():
    """car-greetings committed dist/ under this namespace; the mapping must not move."""
    assert RoleUuids(NAMESPACE)("weather") == "E45BFF59-33DD-51F5-A5E1-88F89C176267"


def test_role_uuids_are_not_repeating_hex_placeholders():
    """The validator hard-rejects repeating-hex UUIDs."""
    body = RoleUuids(NAMESPACE)("weather").replace("-", "")
    assert len(set(body)) > 1


def test_many():
    u = RoleUuids(NAMESPACE)
    assert u.many(["a", "b"]) == {"a": u("a"), "b": u("b")}
