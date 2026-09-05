from __future__ import annotations

from typing import Any, TypedDict

from pydantic import BaseModel, Field


class TaskPlan(BaseModel):
    summary: str
    implementation_steps: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    likely_files: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class DocumentationUpdate(BaseModel):
    path: str
    content: str


class DocumentationResult(BaseModel):
    summary: str = ""
    updates: list[DocumentationUpdate] = Field(default_factory=list)


class ReviewFinding(BaseModel):
    severity: str = "medium"
    file: str = ""
    line: int | None = None
    message: str


class ReviewResult(BaseModel):
    passed: bool
    summary: str
    findings: list[ReviewFinding] = Field(default_factory=list)


class WorkflowState(TypedDict, total=False):
    request: str
    repo_path: str
    test_command: str
    task_id: str
    branch_name: str
    worktree_path: str
    plan: dict[str, Any]
    code_result: dict[str, Any]
    test_report: dict[str, Any]
    docs_result: dict[str, Any]
    review: dict[str, Any]
    retry_count: int
    max_retries: int
    feedback: str
    documentation_feedback: str
    approval: str
    status: str
    commit: str
    final_summary: str
