"""N4 per-vendor MPN grammar registry (spec C.3.1, ADR-0019 rule 3).

Decoders validate a token is a plausible MPN for their vendor and derive
family/capacity/generation ONLY — variant semantics come from the catalog,
never the code string. Each result carries a provenance tier that flows into
rung-2 confidence."""

from __future__ import annotations

from collections.abc import Callable

from hw_radar.matching.grammars import seagate, toshiba, wd
from hw_radar.matching.types import DecodeResult

_DECODERS: tuple[Callable[[str], DecodeResult | None], ...] = (
    seagate.decode,
    wd.decode,
    toshiba.decode,
)

# Every manufacturer key a decoder can emit (DecodeResult.vendor). The resolver
# creates these manufacturers on a rung-2 decode without any seed, so they are
# valid label keys even when no seed document names them (HGST: the owner's Q3
# ruling keeps HUS/HUH drives under `hgst`). The eval harness admits them;
# tests/unit/test_grammars.py pins that each decoder stays inside this set.
DECODER_VENDORS: frozenset[str] = frozenset({"seagate", "western_digital", "hgst", "toshiba"})


def decode(normalized_token: str) -> DecodeResult | None:
    for decoder in _DECODERS:
        result = decoder(normalized_token)
        if result is not None:
            return result
    return None
