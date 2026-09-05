from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Literal

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .codex_agent import CodexAgent
from .config import Settings
from .git_ops import (
    changed_files,
    commit_all,
    create_worktree,
    diff_text,
    repository_root,
    require_clean_repository,
    write_document,
)
from .llm import StructuredModelClient
from .state import DocumentationResult, ReviewResult, TaskPlan, WorkflowState
from .test_runner import detect_test_command, run_tests


def build_graph(settings: Settings, checkpointer=None):
    settings.ensure_runtime_directories()
    model_client = StructuredModelClient(settings)
    codex = CodexAgent(settings)

    def prepare(state: WorkflowState) -> dict:
        repo = repository_root(Path(state["repo_path"]))
        require_clean_repository(repo)
        task_id = state.get("task_id") or uuid.uuid4().hex[:12]
        branch_name = state.get("branch_name") or f"agent/{task_id}"
        worktree = settings.worktree_home / task_id
        create_worktree(repo, worktree, branch_name)
        test_command = state.get("test_command", "").strip()
        if not test_command:
            test_command = detect_test_command(worktree)
        return {
            "repo_path": str(repo),
            "task_id": task_id,
            "branch_name": branch_name,
            "worktree_path": str(worktree),
            "test_command": test_command,
            "retry_count": 0,
            "max_retries": state.get("max_retries", settings.max_retries),
            "status": "planning",
        }

    def plan_task(state: WorkflowState) -> dict:
        worktree = Path(state["worktree_path"])
        tracked = _tracked_files(worktree)
        prompt = _planning_prompt(
            state,
            tracked_files=tracked,
            approved_context=_approved_planning_context(worktree),
        )
        plan = model_client.generate(
            settings.planner_model,
            "You are a senior software delivery planner. Return a precise, conservative plan.",
            prompt,
            TaskPlan,
            mode=settings.planner_api_mode,
        )
        return {"plan": plan.model_dump(), "status": "coding"}

    def code_task(state: WorkflowState) -> dict:
        result = codex.implement(
            worktree=Path(state["worktree_path"]),
            request=state["request"],
            plan=state["plan"],
            test_command=state["test_command"],
            feedback=state.get("feedback", ""),
        )
        return {"code_result": result, "status": "testing", "feedback": ""}

    def test_task(state: WorkflowState) -> dict:
        report = run_tests(
            Path(state["worktree_path"]),
            state["test_command"],
            settings.test_timeout_seconds,
        )
        return {"test_report": report, "status": "documenting" if report["passed"] else "retrying"}

    def write_docs(state: WorkflowState) -> dict:
        worktree = Path(state["worktree_path"])
        diff = diff_text(worktree)
        documentation_paths = _documentation_paths(worktree)
        documentation_context = _documentation_context(worktree)
        prompt = _documentation_prompt(
            state,
            diff=diff,
            documentation_context=documentation_context,
            documentation_paths=documentation_paths,
        )
        result = model_client.generate(
            settings.docs_model,
            "You are a technical writer. Be accurate to the supplied diff and test evidence.",
            prompt,
            DocumentationResult,
            mode=settings.docs_api_mode,
        )
        if len(result.updates) > 12:
            raise ValueError("Documentation agent requested too many file updates")
        for update in result.updates:
            if len(update.content) > 250_000:
                raise ValueError(f"Documentation update is too large: {update.path}")
            write_document(worktree, update.path, update.content)
        return {
            "docs_result": result.model_dump(),
            "documentation_feedback": "",
            "status": "reviewing",
        }

    def review_task(state: WorkflowState) -> dict:
        worktree = Path(state["worktree_path"])
        diff = diff_text(worktree)
        prompt = f"""Review this software change independently. Focus on correctness, security,
behavioral regressions, missing tests, and documentation accuracy.

Request:
{state['request']}

Acceptance plan:
{json.dumps(state['plan'], ensure_ascii=False, indent=2)}

Test report:
{json.dumps(state['test_report'], ensure_ascii=False, indent=2)}

Changed files:
{json.dumps(changed_files(worktree), ensure_ascii=False)}

Diff:
{diff}

Set passed=true only when the request and acceptance criteria are satisfied, tests passed, and no
high- or medium-severity actionable finding remains. Findings must be concrete and reference a file
when possible. Do not propose unrelated refactors.
"""
        result = model_client.generate(
            settings.review_model,
            "You are a strict read-only code reviewer. Never claim to have modified files.",
            prompt,
            ReviewResult,
            mode=settings.review_api_mode,
        )
        return {"review": result.model_dump(), "status": "awaiting_approval" if result.passed else "retrying"}

    def prepare_test_retry(state: WorkflowState) -> dict:
        report = state["test_report"]
        return {
            "retry_count": state.get("retry_count", 0) + 1,
            "feedback": (
                f"Automated tests failed. Command: {report['command']}\n"
                f"Exit code: {report['exit_code']}\nOutput:\n{report['output']}"
            )[-20_000:],
            "status": "coding",
        }

    def fail_workflow(state: WorkflowState) -> dict:
        source = "tests" if not state.get("test_report", {}).get("passed", False) else "review"
        return {
            "status": "failed",
            "final_summary": (
                f"Workflow stopped after {state.get('retry_count', 0)} retries because {source} "
                f"did not pass. Work remains on branch {state['branch_name']} in "
                f"{state['worktree_path']}."
            ),
        }

    def request_approval(state: WorkflowState) -> dict:
        decision = interrupt(
            {
                "type": "approval_required",
                "request": state["request"],
                "branch": state["branch_name"],
                "worktree": state["worktree_path"],
                "changed_files": changed_files(Path(state["worktree_path"])),
                "test_report": state["test_report"],
                "review": state["review"],
                "message": "Approve to create a commit on the isolated branch; this will not merge or push.",
            }
        )
        approved = bool(decision.get("approved")) if isinstance(decision, dict) else bool(decision)
        if not approved:
            return {
                "approval": "rejected",
                "status": "rejected",
                "final_summary": (
                    f"Approval rejected. Uncommitted changes remain in {state['worktree_path']}."
                ),
            }
        commit = commit_all(Path(state["worktree_path"]), state["request"])
        return {
            "approval": "approved",
            "commit": commit,
            "status": "completed",
            "final_summary": (
                f"Approved changes were committed as {commit} on branch {state['branch_name']}. "
                "The branch was not merged or pushed."
            ),
        }

    graph = StateGraph(WorkflowState)
    graph.add_node("prepare", prepare)
    graph.add_node("plan", plan_task)
    graph.add_node("code", code_task)
    graph.add_node("test", test_task)
    graph.add_node("docs", write_docs)
    graph.add_node("review", review_task)
    graph.add_node("test_retry", prepare_test_retry)
    graph.add_node("review_retry", _prepare_review_retry_update)
    graph.add_node("failed", fail_workflow)
    graph.add_node("approval", request_approval)

    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "plan")
    graph.add_edge("plan", "code")
    graph.add_edge("code", "test")
    graph.add_conditional_edges(
        "test",
        _test_route,
        {"docs": "docs", "retry": "test_retry", "failed": "failed"},
    )
    graph.add_edge("test_retry", "code")
    graph.add_edge("docs", "review")
    graph.add_conditional_edges(
        "review",
        _review_route,
        {"approval": "approval", "retry": "review_retry", "failed": "failed"},
    )
    graph.add_edge("review_retry", "code")
    graph.add_edge("failed", END)
    graph.add_edge("approval", END)
    return graph.compile(checkpointer=checkpointer)


def _test_route(state: WorkflowState) -> Literal["docs", "retry", "failed"]:
    if state["test_report"]["passed"]:
        return "docs"
    if state.get("retry_count", 0) < state.get("max_retries", 2):
        return "retry"
    return "failed"


def _review_route(state: WorkflowState) -> Literal["approval", "retry", "failed"]:
    if state["review"]["passed"]:
        return "approval"
    if state.get("retry_count", 0) < state.get("max_retries", 2):
        return "retry"
    return "failed"


def _prepare_review_retry_update(state: WorkflowState) -> dict:
    feedback = json.dumps(state["review"], ensure_ascii=False, indent=2)[-20_000:]
    return {
        "retry_count": state.get("retry_count", 0) + 1,
        "feedback": feedback,
        "documentation_feedback": feedback,
        "status": "coding",
    }


def _planning_prompt(
    state: WorkflowState,
    *,
    tracked_files: str,
    approved_context: str,
) -> str:
    return f"""Create an implementation plan for a software repository task.

User request:
{state['request']}

Repository files (up to 400):
{tracked_files}

Approved specifications and implementation plans (authoritative when present):
{approved_context}

Test command that will be run:
{state['test_command']}

Follow approved specifications and plans exactly. Do not expand the scope or replace their API,
file layout, output contract, or acceptance criteria with alternatives. Separate implementation
work from documentation work. Make acceptance criteria objective and testable. Do not invent files
or APIs that are not supported by the repository evidence.
"""


def _documentation_prompt(
    state: WorkflowState,
    *,
    diff: str,
    documentation_context: str,
    documentation_paths: list[str],
) -> str:
    review_feedback = state.get("documentation_feedback", "") or "[No previous review feedback]"
    return f"""Update documentation for a completed code change.

Original request:
{state['request']}

Plan:
{json.dumps(state['plan'], ensure_ascii=False, indent=2)}

Test report:
{json.dumps(state['test_report'], ensure_ascii=False, indent=2)}

Previous reviewer feedback that documentation must address:
{review_feedback}

Existing documentation paths (authoritative):
{json.dumps(documentation_paths, ensure_ascii=False, indent=2)}

Any documentation path not listed does not currently exist.
Treat code blocks or examples inside a specification as reference text, not repository files.
The same rule applies to implementation plans.

Current repository diff:
{diff}

Current documentation files (complete contents where size permits):
{documentation_context}

Return complete replacement contents only for documentation files that genuinely need changes.
Allowed paths are README/CHANGELOG Markdown-style files or files under docs/. Never return source,
test, executable, configuration, secret, lock, or environment files. Preserve the repository's
existing documentation language and style. If the request or acceptance criteria require a README
and no README path exists above, create README.md. Address every applicable reviewer finding. If no
documentation update is warranted, return an empty updates array and explain why in summary.
"""


def _tracked_files(worktree: Path) -> str:
    from .git_ops import git

    output = git(worktree, "ls-files")
    lines = output.splitlines()
    shown = lines[:400]
    suffix = f"\n... {len(lines) - 400} more files" if len(lines) > 400 else ""
    return "\n".join(shown) + suffix


def _approved_planning_context(worktree: Path, max_chars: int = 120_000) -> str:
    from .git_ops import git, safe_workspace_path

    paths = []
    for relative in git(worktree, "ls-files").splitlines():
        normalized = relative.replace("\\", "/").lower()
        if normalized.startswith("docs/superpowers/specs/") or normalized.startswith(
            "docs/superpowers/plans/"
        ):
            paths.append(relative)

    blocks: list[str] = []
    size = 0
    for relative in sorted(paths):
        target = safe_workspace_path(worktree, relative)
        if not target.is_file() or target.stat().st_size > max_chars:
            continue
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        block = f"\n--- {relative} ---\n{content}\n"
        if size + len(block) > max_chars:
            break
        blocks.append(block)
        size += len(block)
    return "".join(blocks) or "[No approved specifications or implementation plans found]"


def _documentation_paths(worktree: Path) -> list[str]:
    from .git_ops import git, is_documentation_path

    tracked = git(worktree, "ls-files").splitlines()
    changed = changed_files(worktree)
    return sorted({path for path in [*tracked, *changed] if is_documentation_path(path)})


def _documentation_context(worktree: Path, max_chars: int = 80_000) -> str:
    from .git_ops import safe_workspace_path

    paths = _documentation_paths(worktree)
    preferred = sorted(
        paths,
        key=lambda value: (
            0 if Path(value).name.lower().startswith("readme") else 1,
            0 if value.lower().startswith("docs/") else 1,
            value.lower(),
        ),
    )
    blocks: list[str] = []
    size = 0
    for relative in preferred:
        target = safe_workspace_path(worktree, relative)
        if not target.is_file() or target.stat().st_size > 100_000:
            continue
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        block = f"\n--- {relative} ---\n{content}\n"
        if size + len(block) > max_chars:
            break
        blocks.append(block)
        size += len(block)
    return "".join(blocks) or "[No tracked documentation files found]"
