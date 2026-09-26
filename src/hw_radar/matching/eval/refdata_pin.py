"""Identity of the drive reference catalog a ratification run evaluated against.

Owner decision 2026-09-26 (ledger L6): the composite ADR-0019 gate evaluates
against the canonical first-party drive reference dataset imported through the
production refdata path, never a hand-maintained test catalog. A ratification is
therefore only meaningful for ONE catalog: the same corpus can pass on one seed
revision and fail on the next (a new alias turns a `none` into an accept). This
digest names that catalog so a corpus manifest can pin it
(`CorpusMeta.refdata_drive_digest`) and the gate can refuse a mismatch.

Digest definition — sha256 over the sorted `(filename, sha256(file bytes))`
pairs of every seed document whose category is `drive`, one
`<name>\\0<hex>\\n` line per document. File bytes, not the parsed model: a
whitespace-only edit changes the digest, which is the conservative direction
(a false "catalog changed" costs a re-run; a false "unchanged" ratifies against
a catalog nobody evaluated). The filename is included so a rename or a split of
one family document into two is visible too.

Category isolation: cpu/gpu/ram documents never enter the digest, so growing the
non-drive catalog does not invalidate a drive ratification. The category is read
through the production `SeedDocument` contract (an absent `category` means
`drive`), never by sniffing the JSON, so the digest and the importer cannot
disagree about which documents are drive documents.

Pure file I/O: no Django, safe for unit tests.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from hw_radar.refdata.contracts import SeedDocument
from hw_radar.refdata.loader import SEED_DIR

DRIVE_CATEGORY = "drive"


def drive_seed_paths(seed_dir: Path | None = None) -> list[Path]:
    """Return the drive-category seed documents in `seed_dir` (default: the
    committed production seeds), sorted by filename.

    Raises FileNotFoundError when the directory holds no seed documents at all,
    mirroring `load_seed_documents`; an empty directory must never digest to a
    stable value a manifest could pin.
    """
    directory = seed_dir if seed_dir is not None else SEED_DIR
    # Same `*.json` selection as refdata.loader.load_seed_documents — the digest
    # must cover exactly the files `import_refdata` imports, no more, no fewer.
    paths = sorted(directory.glob("*.json"))
    if not paths:
        msg = f"no seed documents found in {directory}"
        raise FileNotFoundError(msg)
    return [
        path
        for path in paths
        if SeedDocument.model_validate_json(path.read_bytes()).category == DRIVE_CATEGORY
    ]


def drive_seed_digest(seed_dir: Path | None = None) -> str:
    """Return the hex sha256 identity of the drive seed documents (see module
    docstring for the exact definition)."""
    lines = "".join(
        f"{path.name}\0{hashlib.sha256(path.read_bytes()).hexdigest()}\n"
        for path in drive_seed_paths(seed_dir)
    )
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()
