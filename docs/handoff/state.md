# Handoff State

## Current focus

- MS-1e (PR #20, release `1099f766`) is merged to `main` and deployed; healthz-verified 2026-08-16.
- Code-side go-live gates cleared, pushed on `dev` (5a7f5b7): retention sweeper, eBay CR-004 delete-on-delist, lane-state split (ADR-0020).
- Next: the owner-in-the-loop ratification step (design §6), then deliberate per-source enables.
- Every merge to `main` needs deployment approval or the run dies at 30 days; use `HW_RADAR_DB_PORT=5433` locally.

## Active incidents

- None.
