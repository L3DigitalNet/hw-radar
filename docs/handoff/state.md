# Handoff State

## Current focus

- Matcher `2026.09.3` (migration 0024, evaluator ms2c.3) releases via dev->main; prod stays fail-closed for Apify.
- Owner gates: OQ33 (legacy drive families; last ADR-0019 audit blocker), OQ34 (CPU auto_accept scope).
- F5a: run the proof-env tick after 2026-10-02 21:10Z; verify 0/0; handoff only if paid admission wanted.
- F6 (eBay x CPU x EPYC) blocked on OQ34 + per-cell checklist; its watch must set require_vendor_unlocked.

## Active incidents

- None.
