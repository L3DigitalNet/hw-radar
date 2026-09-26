"""ADR-0019 matching layer: pure extraction library + ladder; resolver.py is the
only module that touches the ORM (spec C.3 "pure-function library plus a resolver
service"). Import-light on purpose — submodules are imported explicitly."""

# Stamped on every listing_resolution edge (C.3.3). Bump on ANY rule change —
# vocab pattern, grammar rule, ladder constant — so re-resolution runs are
# diffable experiments (C.3.5). Format: YYYY.MM.revision.
#
# Rule history, so a persisted listing_resolution.matcher_version stays
# interpretable after the rules move on (earlier bumps: git log of this file):
#   2026.09.1 — MS-2 multi-category watch core rules; drive rules as MS-1e
#               evaluated them (docs/evidence/2026-09-25-ms1e-audit-packet.md).
#   2026.09.2 — MS-1e owner-audit false-merge fixes: (a) an MPN cited only as the
#               object of a comparison phrase ("comparable to X") is masked and
#               never becomes identity evidence; (b) the Seagate `nm` segment no
#               longer implies Exos (grammars/seagate.py), so ST…NM… tokens
#               decode without a family and never attach at rung 2; (c) WD
#               recertified-store SKU keys expose the manufacturer MPN as a
#               structured-field candidate. Edges stamped 2026.09.1 may carry
#               rung-2 Seagate/Exos families that 2026.09.2 would not assert.
#               2026.09.2 also: contradicted exact alias -> review; older-version
#               automated priors re-decided.
#               2026.09.2 also: reference spans computed before punctuation
#               stripping; offer terms masked.
MATCHER_VERSION = "2026.09.2"
