"""Shared fixtures: real builds from the two projects this library came out of."""

from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    with (FIXTURES / name).open("rb") as handle:
        return plistlib.load(handle)


@pytest.fixture
def attendance() -> dict:
    """Brightwheel Attendance: 251 actions, nested Repeats, a menu, every idiom."""
    return load_fixture("attendance.xml")


@pytest.fixture
def check_in() -> dict:
    """Brightwheel Check In: a wrapper that runs Attendance by name."""
    return load_fixture("check-in.xml")


@pytest.fixture
def car_greetings() -> dict:
    """Car Greetings: deterministic UUIDs, a prompt with eight attachments, dictionary reads."""
    return load_fixture("car-greetings.xml")


@pytest.fixture
def car_greetings_trigger() -> dict:
    return load_fixture("car-greetings-trigger.xml")


@pytest.fixture
def publisher_baseline() -> dict:
    """Brightwheel Share Links as built before this library existed."""
    return load_fixture("publisher.xml")
