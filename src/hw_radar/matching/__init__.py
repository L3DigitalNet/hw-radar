"""ADR-0019 matching layer: pure extraction library + ladder; resolver.py is the
only module that touches the ORM (spec C.3 "pure-function library plus a resolver
service"). Import-light on purpose — submodules are imported explicitly."""

# Stamped on every listing_resolution edge (C.3.3). Bump on ANY rule change —
# vocab pattern, grammar rule, ladder constant — so re-resolution runs are
# diffable experiments (C.3.5). Format: YYYY.MM.revision.
MATCHER_VERSION = "2026.09.1"
