from __future__ import annotations

import re
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


def run_command(
    args: list[str],
    *,
    cwd: Path,
    timeout: int = 120,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired as error:
        raise GitError(f"Command timed out after {timeout}s: {' '.join(args)}") from error
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise GitError(f"Command failed ({result.returncode}): {' '.join(args)}\n{detail}")
    return result


def git(repo: Path, *args: str, timeout: int = 120, check: bool = True) -> str:
    result = run_command(["git", *args], cwd=repo, timeout=timeout, check=check)
    return result.stdout.strip()


def repository_root(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise GitError(f"Repository path does not exist: {resolved}")
    root = git(resolved, "rev-parse", "--show-toplevel")
    return Path(root).resolve()


def require_clean_repository(repo: Path) -> None:
    status = git(repo, "status", "--porcelain")
    if status:
        raise GitError(
            "The source repository has uncommitted changes. Commit or stash them before starting "
            "an isolated agent worktree."
        )


def create_worktree(repo: Path, destination: Path, branch: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise GitError(f"Worktree destination already exists: {destination}")
    git(repo, "worktree", "add", "-b", branch, str(destination), "HEAD", timeout=300)


def changed_files(worktree: Path) -> list[str]:
    output = git(worktree, "ls-files", "--modified", "--others", "--exclude-standard")
    return sorted({line.strip().replace("\\", "/") for line in output.splitlines() if line.strip()})


def is_documentation_path(relative_path: str) -> bool:
    path = Path(relative_path.replace("\\", "/"))
    lowered_parts = [part.lower() for part in path.parts]
    return (
        path.suffix.lower() in {".md", ".mdx", ".rst"}
        or (lowered_parts and lowered_parts[0] in {"docs", "doc", "documentation"})
    )


def discard_documentation_changes(worktree: Path) -> list[str]:
    discarded: list[str] = []
    for relative in changed_files(worktree):
        if not is_documentation_path(relative):
            continue
        tracked = bool(git(worktree, "ls-files", "--", relative))
        if tracked:
            git(worktree, "restore", "--source=HEAD", "--worktree", "--", relative)
        else:
            target = safe_workspace_path(worktree, relative)
            if target.is_file() or target.is_symlink():
                target.unlink()
        discarded.append(relative)
    return discarded


def safe_workspace_path(worktree: Path, relative_path: str) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"Path must stay inside the worktree: {relative_path}")
    root = worktree.resolve()
    target = (root / candidate).resolve(strict=False)
    if not target.is_relative_to(root):
        raise ValueError(f"Path must stay inside the worktree: {relative_path}")
    if target.exists() and target.is_symlink():
        raise ValueError(f"Refusing to overwrite a symlink: {relative_path}")
    return target


def write_document(worktree: Path, relative_path: str, content: str) -> None:
    if not is_documentation_path(relative_path):
        raise ValueError(f"Documentation agent cannot write this path: {relative_path}")
    target = safe_workspace_path(worktree, relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content.rstrip() + "\n", encoding="utf-8")


def diff_text(worktree: Path, max_chars: int = 80_000) -> str:
    # Intent-to-add makes untracked text files visible in git diff without staging content.
    git(worktree, "add", "-N", "--", ".", check=False)
    diff = git(worktree, "diff", "--no-ext-diff", "--unified=3", timeout=180)
    if len(diff) <= max_chars:
        return diff
    return diff[:max_chars] + "\n\n[diff truncated by workflow]"


def commit_all(worktree: Path, request: str) -> str:
    if not changed_files(worktree):
        raise GitError("There are no changes to commit.")
    subject = re.sub(r"\s+", " ", request).strip()[:68] or "Apply specialist agent changes"
    git(worktree, "add", "-A")
    git(
        worktree,
        "-c",
        "user.name=Specialist Agent Workflow",
        "-c",
        "user.email=specialist-agent@localhost",
        "commit",
        "-m",
        subject,
        timeout=300,
    )
    return git(worktree, "rev-parse", "HEAD")

