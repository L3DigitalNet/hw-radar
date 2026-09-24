# hw-radar-synthetic-collector image (MS2-D-38). Build context: the Actor root
# (the directory holding .actor/), so COPY paths below are Actor-root relative.
#
# Base: apify/actor-python, the slim Python image with no browsers; the tag's
# Python must equal pyproject's requires-python floor and the [tool.ruff]
# target-version (py314). Dependencies come from uv.lock, not the base image's
# bundled SDK: the lock is what the gate type-checked, tested, and audited.
FROM apify/actor-python:3.14

COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /usr/src/app

# Dependencies first, so source edits reuse the cached dependency layer.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

# contract/ must sit beside src/: synthetic_collector.contract resolves the schema
# files two levels above the package, which only holds for this editable layout.
COPY contract ./contract
COPY src ./src
RUN uv sync --locked --no-dev

CMD ["/usr/src/app/.venv/bin/python", "-m", "synthetic_collector"]
