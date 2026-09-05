import specialist_workflow.graph as workflow_graph
from specialist_workflow.graph import _review_route, _test_route


def test_passed_tests_continue_to_docs() -> None:
    assert _test_route({"test_report": {"passed": True}}) == "docs"


def test_failed_tests_retry_with_budget() -> None:
    state = {"test_report": {"passed": False}, "retry_count": 1, "max_retries": 2}
    assert _test_route(state) == "retry"


def test_failed_tests_stop_when_budget_is_exhausted() -> None:
    state = {"test_report": {"passed": False}, "retry_count": 2, "max_retries": 2}
    assert _test_route(state) == "failed"


def test_review_routing() -> None:
    assert _review_route({"review": {"passed": True}}) == "approval"
    assert _review_route(
        {"review": {"passed": False}, "retry_count": 0, "max_retries": 2}
    ) == "retry"
    assert _review_route(
        {"review": {"passed": False}, "retry_count": 2, "max_retries": 2}
    ) == "failed"


def test_review_retry_preserves_feedback_for_documentation_agent() -> None:
    state = {
        "review": {
            "passed": False,
            "summary": "README is missing",
            "findings": [
                {
                    "severity": "medium",
                    "file": "README.md",
                    "message": "Create the required README",
                }
            ],
        },
        "retry_count": 0,
    }

    update = workflow_graph._prepare_review_retry_update(state)

    assert update["retry_count"] == 1
    assert "README is missing" in update["feedback"]
    assert update["documentation_feedback"] == update["feedback"]


def test_documentation_prompt_names_existing_paths_and_review_feedback() -> None:
    state = {
        "request": "Create the required README",
        "plan": {"acceptance_criteria": ["README.md exists"]},
        "test_report": {"passed": True},
        "documentation_feedback": "Reviewer: README.md is missing",
    }

    prompt = workflow_graph._documentation_prompt(
        state,
        diff="diff --git a/tool.py b/tool.py",
        documentation_context="--- docs/spec.md ---\napproved spec",
        documentation_paths=["docs/spec.md"],
    )

    assert "README.md is missing" in prompt
    assert '"docs/spec.md"' in prompt
    assert "not listed does not currently exist" in prompt
    assert "code blocks or examples inside a specification" in prompt


def test_planning_prompt_includes_approved_plan_contents_as_authoritative() -> None:
    state = {
        "request": "Build the PLC analyzer",
        "test_command": "python -m unittest discover -s tests -v",
    }

    prompt = workflow_graph._planning_prompt(
        state,
        tracked_files="docs/superpowers/plans/plc.md",
        approved_context="The CLI accepts exactly one input file.",
    )

    assert "The CLI accepts exactly one input file." in prompt
    assert "authoritative" in prompt
    assert "Do not expand the scope" in prompt
