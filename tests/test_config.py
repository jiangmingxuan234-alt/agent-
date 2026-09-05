import json
from pathlib import Path

from specialist_workflow.config import Settings


def test_explicit_settings_take_priority(tmp_path: Path) -> None:
    settings = Settings(
        ai_api_key="secret",
        ai_base_url="https://example.test/v1/",
        state_db=tmp_path / "state.sqlite",
        worktree_home=tmp_path / "worktrees",
        codex_command=r"C:\nodejs\node_global\codex.cmd",
    )
    assert settings.resolved_api_key() == "secret"
    assert settings.resolved_base_url() == "https://example.test/v1"
    assert settings.planner_api_mode == "responses"
    assert settings.docs_api_mode == "chat_completions"
    assert settings.review_api_mode == "responses"


def test_reads_codex_auth_without_printing_key(tmp_path: Path, monkeypatch) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text(
        json.dumps({"OPENAI_API_KEY": "hidden-value"}), encoding="utf-8"
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    settings = Settings(ai_base_url="https://example.test/v1")
    assert settings.resolved_api_key() == "hidden-value"
