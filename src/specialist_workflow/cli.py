from __future__ import annotations

import json
import uuid
from pathlib import Path

import typer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .config import Settings
from .graph import build_graph
from .git_ops import diff_text

app = typer.Typer(no_args_is_help=True, help="Run a guarded specialist multi-agent workflow.")
console = Console()


@app.command()
def doctor() -> None:
    """Validate local tools and show non-secret model configuration."""
    settings = Settings()
    summary = settings.public_summary()
    settings.resolved_api_key()
    table = Table(title="Specialist workflow configuration")
    table.add_column("Setting")
    table.add_column("Value")
    for key, value in summary.items():
        table.add_row(key, value)
    table.add_row("api_key", "available (hidden)")
    console.print(table)


@app.command()
def run(
    repo: Path = typer.Option(..., exists=True, file_okay=False, resolve_path=True),
    request: str = typer.Option(..., "--request", "-r"),
    test_command: str = typer.Option("", help="Repository test command; auto-detected if omitted."),
    thread_id: str = typer.Option("", help="Optional stable workflow ID."),
) -> None:
    """Start a workflow and pause before creating the final commit."""
    settings = Settings()
    settings.ensure_runtime_directories()
    selected_thread = thread_id or uuid.uuid4().hex
    config = {"configurable": {"thread_id": selected_thread}}
    initial = {
        "request": request,
        "repo_path": str(repo),
        "test_command": test_command,
        "max_retries": settings.max_retries,
    }
    with SqliteSaver.from_conn_string(str(settings.state_db)) as checkpointer:
        graph = build_graph(settings, checkpointer)
        result = graph.invoke(initial, config=config)
        snapshot = graph.get_state(config)
    _print_result(selected_thread, result, snapshot.interrupts)


@app.command()
def resume(
    thread_id: str = typer.Argument(...),
    approve: bool = typer.Option(False, "--approve", help="Approve creation of an isolated commit."),
    reject: bool = typer.Option(False, "--reject", help="Reject and leave changes uncommitted."),
) -> None:
    """Resume a workflow waiting for approval."""
    if approve == reject:
        raise typer.BadParameter("Choose exactly one of --approve or --reject")
    settings = Settings()
    config = {"configurable": {"thread_id": thread_id}}
    with SqliteSaver.from_conn_string(str(settings.state_db)) as checkpointer:
        graph = build_graph(settings, checkpointer)
        snapshot = graph.get_state(config)
        if not snapshot.values:
            raise typer.BadParameter(f"No checkpoint found for thread {thread_id}")
        result = graph.invoke(Command(resume={"approved": approve}), config=config)
        final_snapshot = graph.get_state(config)
    _print_result(thread_id, result, final_snapshot.interrupts)


@app.command()
def status(thread_id: str = typer.Argument(...)) -> None:
    """Show persisted workflow state without exposing secrets."""
    settings = Settings()
    config = {"configurable": {"thread_id": thread_id}}
    with SqliteSaver.from_conn_string(str(settings.state_db)) as checkpointer:
        graph = build_graph(settings, checkpointer)
        snapshot = graph.get_state(config)
    if not snapshot.values:
        raise typer.BadParameter(f"No checkpoint found for thread {thread_id}")
    safe = {
        key: value
        for key, value in snapshot.values.items()
        if key not in {"code_result"}
    }
    console.print_json(json.dumps(safe, ensure_ascii=False, default=str))
    if snapshot.interrupts:
        console.print(Panel("Workflow is waiting for approval.", style="yellow"))


@app.command("inspect")
def inspect_workflow(thread_id: str = typer.Argument(...)) -> None:
    """Show the complete pending diff before approval."""
    settings = Settings()
    config = {"configurable": {"thread_id": thread_id}}
    with SqliteSaver.from_conn_string(str(settings.state_db)) as checkpointer:
        graph = build_graph(settings, checkpointer)
        snapshot = graph.get_state(config)
    if not snapshot.values:
        raise typer.BadParameter(f"No checkpoint found for thread {thread_id}")
    worktree_value = snapshot.values.get("worktree_path")
    if not worktree_value:
        raise typer.BadParameter("Workflow has not created a worktree yet")
    diff = diff_text(Path(worktree_value), max_chars=1_000_000)
    console.print(diff or "[dim]No pending diff.[/dim]")


def _print_result(thread_id: str, result: dict, interrupts: tuple) -> None:
    console.print(f"\n[bold]Thread ID:[/bold] {thread_id}")
    if interrupts:
        payload = interrupts[0].value
        console.print(Panel("Workflow paused for human approval", style="yellow"))
        console.print_json(json.dumps(payload, ensure_ascii=False, default=str))
        console.print(f"\nApprove: specialist-workflow resume {thread_id} --approve")
        console.print(f"Reject:  specialist-workflow resume {thread_id} --reject")
        return
    status_value = result.get("status", "unknown")
    summary = result.get("final_summary", "Workflow finished.")
    console.print(Panel(summary, title=status_value, style="green" if status_value == "completed" else "red"))


if __name__ == "__main__":
    app()
