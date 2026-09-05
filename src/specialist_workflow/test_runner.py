from __future__ import annotations

import subprocess
from pathlib import Path


def detect_test_command(worktree: Path) -> str:
    if (worktree / "pyproject.toml").exists() or (worktree / "pytest.ini").exists():
        return "python -m pytest"
    if (worktree / "package.json").exists():
        return "npm.cmd test -- --run"
    if (worktree / "Cargo.toml").exists():
        return "cargo test"
    if (worktree / "go.mod").exists():
        return "go test ./..."
    raise RuntimeError("Could not detect tests. Pass --test-command explicitly.")


def run_tests(worktree: Path, command: str, timeout: int) -> dict:
    selected = command.strip() or detect_test_command(worktree)
    try:
        result = subprocess.run(
            selected,
            cwd=worktree,
            shell=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
        )
        output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
        return {
            "command": selected,
            "passed": result.returncode == 0,
            "exit_code": result.returncode,
            "output": output[-20_000:],
        }
    except subprocess.TimeoutExpired as error:
        output = ((error.stdout or "") + "\n" + (error.stderr or "")).strip()
        return {
            "command": selected,
            "passed": False,
            "exit_code": -1,
            "output": f"Timed out after {timeout}s\n{output}"[-20_000:],
        }

