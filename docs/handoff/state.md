# Handoff State

## Current focus

- **Strategy re-baselined 2026-09-24** by ADRs 0021–0022.
- Immediate execution target is master-spec **MS-2 — Multi-category watch core**, not the old
  MS-2a drive-scoring substrate.
- Preserve the implemented/deployed MS-0/MS-1 foundation and drive matcher; generalize at the
  category/domain and acquisition-provider boundaries rather than rewriting the application.
- First-class v1 categories: HDD/SSD, GPU/accelerator, RAM, CPU. Saved requirements evaluate to
  `match | no_match | unknown`; optional scores are category-local.
- Acquisition direction: cheap official/direct/local collectors stay local; self-owned private
  Apify Actors are added selectively through a provider adapter. Hardware Radar Apify spend is
  hard-capped at **$20/month**, with a **$12/month** initial operating target.
- Next implementation proof: one complete saved-requirement → collection → match/unknown →
  shortlist/evidence → exactly-one-alert path, including one self-owned Actor integration proof.
- Existing MS-2 scoring design rev 14 remains owner-accepted as the advanced drive-scoring
  design; MS-2a plan rev 4 is explicitly deferred and must be rebased before any future execution.
- MS-1e owner-in-the-loop real-corpus ratification still gates acceptance of ADR 0019 / affected
  drive source enablement.
- Production remains at `f3303b1` (migrations 0014–0017 deployed); the strategy PR is
  documentation-only.

## Active incidents

- None.
