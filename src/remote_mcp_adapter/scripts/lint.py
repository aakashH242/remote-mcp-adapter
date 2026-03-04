"""Project lint runner.

Usage:
  uv run lint
  uv run lint --fix
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SUBPROCESS_TIMEOUT_SEC = 300


def _run(command: list[str]) -> int:
    """Print and execute one subprocess command.

    Args:
        command: Command-line tokens to run.

    Returns:
        Process exit code.
    """
    print(f"> {' '.join(command)}")
    completed = subprocess.run(
        command,
        check=False,
        timeout=_SUBPROCESS_TIMEOUT_SEC,
    )
    return int(completed.returncode)


def main() -> int:
    """Run the lint pipeline and return an aggregate exit code.

    Returns:
        Zero on success, non-zero on first failure.
    """
    repo_root = Path(__file__).resolve().parents[3] / "src"
    fix_mode = "--fix" in sys.argv[1:]

    if fix_mode:
        return _run(["ruff", "check", "--fix", str(repo_root)])

    steps = [
        ["black", "--check", str(repo_root)],
        ["ruff", "check", str(repo_root)],
        ["mypy", str(repo_root)],
    ]
    for cmd in steps:
        code = _run(cmd)
        if code != 0:
            return code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
