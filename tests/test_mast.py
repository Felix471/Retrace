from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from retrace.core.mast import (
    FAILURE_MODE_CATEGORIES,
    FAILURE_MODES,
    FAILURE_MODES_BY_ID,
    FailureMode,
    get_failure_mode,
)

EXPECTED_CATEGORIES = (
    "Specification & system design",
    "Inter-agent misalignment",
    "Task verification & termination",
)
EXPECTED_IDS = (
    "1.1",
    "1.2",
    "1.3",
    "1.4",
    "1.5",
    "2.1",
    "2.2",
    "2.3",
    "2.4",
    "2.5",
    "2.6",
    "3.1",
    "3.2",
    "3.3",
)


def test_vocabulary_shape_and_lookup() -> None:
    assert len(FAILURE_MODES) == 14
    assert tuple(mode.id for mode in FAILURE_MODES) == EXPECTED_IDS
    assert set(FAILURE_MODES_BY_ID) == set(EXPECTED_IDS)
    assert tuple(category for category, _ in FAILURE_MODE_CATEGORIES) == EXPECTED_CATEGORIES
    assert tuple(len(modes) for _, modes in FAILURE_MODE_CATEGORIES) == (5, 6, 3)

    grouped = tuple(mode for _, modes in FAILURE_MODE_CATEGORIES for mode in modes)
    assert grouped == FAILURE_MODES
    assert all(mode.category == category for category, modes in FAILURE_MODE_CATEGORIES for mode in modes)
    assert all(mode.description.strip() for mode in FAILURE_MODES)
    assert len({mode.description for mode in FAILURE_MODES}) == 14
    assert all(get_failure_mode(mode.id) is mode for mode in FAILURE_MODES)


def test_failure_modes_are_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        FAILURE_MODES[0].name = "changed"

    assert isinstance(FAILURE_MODES[0], FailureMode)
