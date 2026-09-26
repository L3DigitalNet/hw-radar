"""The refdata pin (owner decision 2026-09-26): the digest that names the drive
catalog a ratification was evaluated against. Pure file I/O over seed documents."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from hw_radar.matching.eval.refdata_pin import drive_seed_digest, drive_seed_paths
from hw_radar.refdata.loader import SEED_DIR, load_seed_documents


@pytest.fixture
def seed_copy(tmp_path: Path) -> Path:
    target = tmp_path / "seeds"
    shutil.copytree(SEED_DIR, target)
    return target


def _first_of_category(seed_dir: Path, *, drive: bool) -> Path:
    drive_paths = set(drive_seed_paths(seed_dir))
    return next(path for path in sorted(seed_dir.glob("*.json")) if (path in drive_paths) is drive)


def test_digest_covers_exactly_the_drive_documents_the_loader_loads() -> None:
    drive_docs = [doc for doc in load_seed_documents() if doc.category == "drive"]
    assert drive_docs, "production seeds must contain drive documents"
    assert len(drive_seed_paths()) == len(drive_docs)


def test_digest_is_the_documented_definition() -> None:
    expected = hashlib.sha256(
        "".join(
            f"{path.name}\0{hashlib.sha256(path.read_bytes()).hexdigest()}\n"
            for path in drive_seed_paths()
        ).encode("utf-8")
    ).hexdigest()
    assert drive_seed_digest() == expected
    assert drive_seed_digest(SEED_DIR) == expected  # default dir IS the production dir


def test_a_non_drive_seed_edit_does_not_move_the_digest(seed_copy: Path) -> None:
    """Category isolation: growing the cpu/gpu/ram catalog must not invalidate a
    drive ratification."""
    before = drive_seed_digest(seed_copy)
    cpu = _first_of_category(seed_copy, drive=False)
    cpu.write_text(cpu.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    (seed_copy / "zz-extra-cpu.json").write_bytes(cpu.read_bytes())
    assert drive_seed_digest(seed_copy) == before


def test_a_drive_seed_edit_moves_the_digest(seed_copy: Path) -> None:
    before = drive_seed_digest(seed_copy)
    drive = _first_of_category(seed_copy, drive=True)
    drive.write_text(drive.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert drive_seed_digest(seed_copy) != before


def test_a_drive_seed_rename_moves_the_digest(seed_copy: Path) -> None:
    before = drive_seed_digest(seed_copy)
    drive = _first_of_category(seed_copy, drive=True)
    drive.rename(seed_copy / f"renamed-{drive.name}")
    assert drive_seed_digest(seed_copy) != before


def test_an_empty_seed_dir_has_no_digest(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        drive_seed_digest(tmp_path)
