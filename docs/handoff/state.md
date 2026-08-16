# Handoff State

## Current focus

- MS-1e (PR #20, release `1099f766`) is merged to `main` and deployed; healthz-verified 2026-08-16.
- Next: the deferred owner-in-the-loop ratification step (design §6) — live harvest through ADR-0019 flip.
- Clear go-live gates first: bounded-retention sweeper, eBay soft-delete (CR-004), lane-state split.
- Every merge to `main` needs deployment approval or the run dies at 30 days; use `HW_RADAR_DB_PORT=5433` locally.

## Active incidents

- None.
