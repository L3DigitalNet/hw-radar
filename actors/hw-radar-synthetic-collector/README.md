# hw-radar-synthetic-collector

The first Hardware Radar Apify Actor. It is private and owned by this
repository (plan decisions MS2-D-38 and MS2-D-42). It fetches synthetic fixture
pages that this public repository commits, from `raw.githubusercontent.com` at
a commit SHA pinned in its input. It then emits the hw-radar Actor contract:
one listing row per fixture listing in the default dataset, and one `OUTPUT`
record in the default key-value store.

The Actor exists to prove the Apify provider path end to end with a
deterministic, project-owned source. No merchant is involved, so no merchant
terms apply. It is never a production collection source.

**Status:** D-prep (plan task D1) built it. Nothing has been pushed, built, or
run on Apify. The first push, build, and smoke run belong to plan task F5a,
through hw-radar's own admission and ledger. See `DEPLOYMENTS.md`.

## Layout

| Path | Role |
| --- | --- |
| `.actor/actor.json` | Actor definition: name, `version` (major = contract major), memory bounds, input-schema reference |
| `.actor/input_schema.json` | The Apify-dialect input schema. It is a view of the contract, and hw-radar's tests pin it to the contract. |
| `.actor/Dockerfile` | Image build from `uv.lock` on `apify/actor-python:3.14` |
| `.actorignore` | Limits what `apify push` uploads: no tests, no fixtures, no virtualenv |
| `contract/hw-radar-{input,listing,run}-v1.schema.json` | **The contract artifact** (JSON Schema draft 2020-12) |
| `src/synthetic_collector/core.py` | Pure core: input, then a bounded fetch plan, then rows plus `OUTPUT` |
| `src/synthetic_collector/main.py` | Thin Apify SDK entry point: pushes rows, then writes `OUTPUT`, then sets the exit status |
| `fixtures/source/` | The synthetic "merchant" pages the Actor fetches (served from GitHub at a pinned commit) |
| `tests/` | Offline tests. `tests/support.py` also generates hw-radar's frozen classifier fixtures. |
| `DEPLOYMENTS.md` | Operator push/build log (MS2-D-43) |

## Contract

hw-radar owns the contract (MS2-D-14). The three schema files under
`contract/` are the single artifact:

- hw-radar's Pydantic models in `src/hw_radar/acquisition/apify/contract.py`
  must produce these files exactly (`tests/unit/test_apify_contract.py`).
- This Actor validates its input and every row against the same files. Its
  tests also validate every emitted `OUTPUT`.

A contract change edits the models, the files, and this Actor in one PR. To
regenerate a schema file, dump `Model.model_json_schema()` as JSON with two-space
indentation and a trailing newline. `CONTRACT_SCHEMA_MODELS` names the model for
each file.

The input carries caps only: `maxItems`, `maxPages`, `maxRequests`, `maxBytes`,
and `timeBudgetSecs`. It has no proxy, browser, CAPTCHA, or unblocker field.
Three input fields belong to this Actor alone and are not in the common input
part: `fixtureCommit`, `fixturePaths`, and `faultMode`.

## Behavior

- **Caps are enforced by the Actor itself.** The platform's `maxItems` run
  option does not apply to a pay-per-usage Actor, so the Actor cannot rely on
  it. A limit counts as *hit* only when it stopped work that remained. Every
  limit binding at the moment the Actor stops is reported. The time budget is
  one wall-clock deadline for the whole run: it is checked before each request
  and after every received body chunk, and each request is also bounded in
  real time by the remaining budget, so a slow-drip response cannot outlast it.
- **Byte counting.** The Actor requests identity encoding and counts
  response-body bytes. If a server compresses anyway, the decoded count is
  larger than the real transfer. That makes the cap stricter, never looser.
- **Storage.** Merchant content goes only to the default dataset. The Actor
  writes exactly one key-value record, `OUTPUT`, which holds counts, scope, and
  errors. The platform stores the run's `INPUT` record in the same store; the
  Actor never writes it. Rows are pushed before `OUTPUT` is written, so
  `OUTPUT` acts as a commit marker.
- **No proxy.** The Actor never constructs an Apify `ProxyConfiguration`. Its
  HTTP client uses `trust_env=False` and `follow_redirects=False`.
  `tests/test_boundaries.py` enforces this, and the ban on Django, `hw_radar`,
  and database imports, by scanning the source.
- **Invalid input.** The Actor fails the run and writes no `OUTPUT` and no rows.

### `faultMode`

Each mode deterministically produces one row of the MS2-D-14 classification
table.

| Mode | Effect |
| --- | --- |
| `none` | An honest fetch |
| `truncate_items` / `truncate_pages` | Lower the cap to 1, so the real cap check trips |
| `truncate_bytes` | The byte budget reaches zero after page 1 |
| `truncate_time` | The time budget is exhausted after page 1 |
| `partial_failure` | The last page is not fetched; an error is recorded |
| `contradictory_report` | Claims `complete` while reporting a hit page limit |
| `count_mismatch` | Reports `itemsEmitted` as one more than the rows pushed |
| `unknown_schema` | Stamps an unknown contract major on `OUTPUT` and on every row |
| `fail` | Emits page 1, writes `OUTPUT` with status `failed`, and fails the run |

### Frozen classifier fixtures

hw-radar's frozen classifier fixtures live in
`tests/fixtures/apify_contract/v1/` at the repository root. Only
`tests/support.py` generates them, from the fault modes above, and
`tests/test_output.py` fails if a committed fixture differs from what the Actor
emits now. Each fixture declares the platform facts the Actor does not control
in `platformEffects`. Those facts come from a closed set: a remote status
override, an absent `OUTPUT` record, an admitted scope that differs from the
run's, and rows taken from an `unknown_schema` run. Every other byte must
regenerate exactly. To regenerate after an intentional change, run this from
this directory:

```bash
uv run python -m tests.support --write
```

## Toolchain

| Item | Choice |
| --- | --- |
| Python | 3.14 (`.python-version`, `requires-python`, ruff `target-version = "py314"`, image tag `apify/actor-python:3.14`) |
| Apify SDK | `apify` 4.0.2, pinned `>=4.0.2,<5` and locked in this project's `uv.lock` only. It never enters hw-radar's root lock. |
| HTTP | `httpx` 0.28.1 |
| Schema validation | `jsonschema` 4.26.0 (Draft 2020-12) |
| Lock | `uv.lock` in this directory, created with uv 0.11.6 |

## Gate

The root gate runs these commands from the repository root. They live in
`scripts/check.py` and in the CI workflow as separate steps.

```bash
uv run --directory actors/hw-radar-synthetic-collector --locked basedpyright
uv run --directory actors/hw-radar-synthetic-collector --locked coverage run -m pytest
uv run --directory actors/hw-radar-synthetic-collector --locked coverage report
uv run --directory actors/hw-radar-synthetic-collector --locked pip-audit
```

They use `--directory` rather than `--project`, because the tools read their
configuration from the working directory. Root `ruff format --check .` and
`ruff check .` already cover this directory. This project's `[tool.ruff]`
extends the root configuration and overrides only `target-version`.

## Sources checked (retrieved 2026-09-24)

- Actor definition fields: `actorSpecification`, `name`, `version`, `buildTag`,
  `defaultMemoryMbytes`, `minMemoryMbytes`, `maxMemoryMbytes`, `input`, and
  `dockerfile`. Source:
  <https://docs.apify.com/actors/development/actor-definition/actor-json>. The
  page defines no run-timeout field. `name`, `version`, and `buildTag` are
  honored only when deploying through the Apify CLI.
- Base image `apify/actor-python`, with Python tags 3.10 through 3.14. A Python
  Actor must ship its own Dockerfile, because without one the platform falls
  back to a Node.js image. Source:
  <https://docs.apify.com/actors/development/actor-definition/dockerfile>.
- SDK entry point (`async with Actor:`, `Actor.get_input`, `Actor.push_data`,
  `Actor.set_value`, `Actor.fail`) and the Python ≥ 3.11 floor. Sources:
  <https://docs.apify.com/sdk/python/docs/concepts/actor-lifecycle> and
  <https://github.com/apify/apify-sdk-python>. The method signatures were
  checked against the installed `apify` 4.0.2.
- SDK version 4.0.2, released 2026-09-03, requiring Python ≥ 3.11. Source:
  <https://pypi.org/project/apify/>.
- Python template layout (`.actor/{actor.json,Dockerfile,input_schema.json}`,
  `src/__main__.py`, `src/main.py`, `requirements.txt`). Corroborated by a
  third-party walkthrough of the `apify create` output.
- Limited-permissions runtime level. Source:
  <https://docs.apify.com/actors/development/permissions>.

## Deliberate deviations from the stock template

- `pyproject.toml` plus `uv.lock` replace `requirements.txt` (MS2-D-38). The
  Dockerfile installs from the lock with a pinned uv binary.
- The code is a named package, `src/synthetic_collector/`, started with
  `python -m synthetic_collector`, instead of the template's `src/main.py`
  package named `src`. The name keeps imports and type checking unambiguous.

## Unconfirmed until F5a (the first real push and run)

- Whether `apify push`, run from this directory, uploads only this directory
  (MS2-D-43). The fallback is a Git-source Actor
  (`<repo>#<branch>:actors/hw-radar-synthetic-collector`) with no push webhook.
- Whether the stock template offers a `pyproject.toml` variant. It does not
  matter here, because the Dockerfile is custom.
- Whether the Dockerfile builds on the platform. It has not been built: D-prep
  runs no Docker or Apify build. The uv image tag `ghcr.io/astral-sh/uv:0.11.6`
  is also unverified against the registry.
- Whether Apify accepts `minItems`/`maxItems` on a `stringList` array and
  applies an input-schema `default` to API-started runs. hw-radar always sends
  every field, so neither affects correctness. Apify's input dialect also cannot
  express the per-item pattern on `fixturePaths`, so the Actor enforces that
  pattern itself against the contract.
- Whether the SDK's `async with Actor:` lifecycle writes any key-value record of
  its own, such as persisted state. This Actor never calls `use_state`. F5a
  lists the default store's keys after a live run.
- Whether platform input validation rejects an invalid input before any
  billable run exists (MS2-D-15, D3).
