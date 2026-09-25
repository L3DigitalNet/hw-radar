# hw-radar-synthetic-collector deployments

One row per `apify push` from a reviewed commit on `dev` or `main` (MS2-D-43).
Actor ids, account identifiers, and tokens are configuration and are never
recorded here.

| Date (UTC) | Git commit | Actor version | Build number | Build tag | Operator reservation | Notes |
| --- | --- | --- | --- | --- | --- | --- |

No build has been pushed. D-prep (plan task D1) performs no Apify push, build,
or run; the first push is plan task F5a.

2026-09-25: the private Actor resource was created empty through the REST API
(operator key; version 1.0, no source files, tag `candidate`, limited permissions,
defaults 256 MB / 300 s / no restart) so the scoped runtime token can be restricted
to it. No build or run exists; the first push is still F5a step 2.
