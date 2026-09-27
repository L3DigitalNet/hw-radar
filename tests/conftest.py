"""Suite-wide fixtures. Pure: nothing here may require the test database."""

from __future__ import annotations

from collections.abc import Callable
from types import MappingProxyType

import pytest

from hw_radar.acquisition import admission


@pytest.fixture
def admit(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Return `admit((source, category), ...)`, which marks those matrix cells ADMITTED.

    The production matrix admits only (ebay, cpu) (acquisition.admission), so
    every test that exercises scheduling mechanics for another cell must admit
    it first. The whole mapping is swapped rather than mutated in place,
    because ADMISSION_MATRIX is a read-only MappingProxyType on purpose.
    Retired sources stay unrunnable whatever this sets: is_retired is checked
    before the matrix.
    """

    def _admit(*cells: tuple[str, str]) -> None:
        patched = dict(admission.ADMISSION_MATRIX)
        for cell in cells:
            patched[cell] = admission.CellStatus.ADMITTED
        monkeypatch.setattr(admission, "ADMISSION_MATRIX", MappingProxyType(patched))

    return _admit


@pytest.fixture
def nothing_admitted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset every ADMITTED cell to NOT_ADMITTED, for tests of the empty-matrix
    behavior (no job, no sweep) that must not depend on which cells the
    production matrix currently admits."""
    patched = {
        cell: admission.CellStatus.NOT_ADMITTED
        if status is admission.CellStatus.ADMITTED
        else status
        for cell, status in admission.ADMISSION_MATRIX.items()
    }
    monkeypatch.setattr(admission, "ADMISSION_MATRIX", MappingProxyType(patched))
