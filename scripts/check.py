"""The local verification gate: root project checks, then each Actor project's own.

Every Hardware Radar Actor under actors/<name>/ is a separate uv project with its
own uv.lock (MS2-D-38), so its type check, tests with coverage, and dependency
audit run in that project's environment, not hw-radar's. Root ruff already
covers actors/. The Actor list is discovered from actors/*/pyproject.toml, so a
new Actor joins the gate without editing this file. CI (.github/workflows/
check.yml) runs the same commands as separate steps and must be kept in step.
"""

import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

COMMANDS: tuple[tuple[str, ...], ...] = (
    ("uv", "run", "ruff", "format", "--check", "."),
    ("uv", "run", "ruff", "check", "."),
    ("uv", "run", "basedpyright"),
    ("uv", "run", "coverage", "run", "-m", "pytest"),
    ("uv", "run", "coverage", "report"),
    ("uv", "run", "pip-audit"),
)

ACTOR_CHECKS: tuple[tuple[str, ...], ...] = (
    ("basedpyright",),
    ("coverage", "run", "-m", "pytest"),
    ("coverage", "report"),
    ("pip-audit",),
)


def actor_projects(root: Path = REPO_ROOT) -> tuple[Path, ...]:
    return tuple(sorted(path.parent for path in (root / "actors").glob("*/pyproject.toml")))


def actor_commands(project: Path) -> tuple[tuple[str, ...], ...]:
    # --directory, not --project: the tools read their configuration from the
    # working directory, so --project alone would type-check and test the Actor
    # under the root's basedpyright/pytest/coverage settings. --locked fails
    # instead of silently re-resolving a stale Actor lockfile.
    relative = project.relative_to(REPO_ROOT).as_posix()
    return tuple(
        ("uv", "run", "--directory", relative, "--locked", *check) for check in ACTOR_CHECKS
    )


def actor_environment() -> dict[str, str]:
    """The environment for Actor commands: the caller's, minus UV_PROJECT_ENVIRONMENT.

    A worker that pins UV_PROJECT_ENVIRONMENT for the root project would otherwise
    make `uv run --directory actors/<name>` sync the Actor's dependencies into the
    root's environment, replacing hw-radar's packages mid-gate. Without it each
    Actor uses its own actors/<name>/.venv.
    """
    env = dict(os.environ)
    env.pop("UV_PROJECT_ENVIRONMENT", None)
    return env


def run_command(command: Sequence[str], env: Mapping[str, str] | None = None) -> int:
    print(f"\n$ {' '.join(command)}", flush=True)
    completed = subprocess.run(command, check=False, cwd=REPO_ROOT, env=env)
    return completed.returncode


def main() -> int:
    for command in COMMANDS:
        return_code = run_command(command)
        if return_code != 0:
            return return_code
    actor_env = actor_environment()
    for project in actor_projects():
        for command in actor_commands(project):
            return_code = run_command(command, env=actor_env)
            if return_code != 0:
                return return_code
    return 0


if __name__ == "__main__":
    sys.exit(main())
