from __future__ import annotations

import json
import os
import shutil
import tomllib
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ai_api_key: SecretStr | None = None
    ai_base_url: str | None = None
    ai_api_mode: str = "auto"
    planner_api_mode: str = "responses"
    docs_api_mode: str = "chat_completions"
    review_api_mode: str = "responses"

    planner_model: str = "gpt-5.6-terra"
    code_model: str = "gpt-5.6-sol"
    code_reasoning_effort: str = "xhigh"
    docs_model: str = "claude-sonnet-4-6"
    review_model: str = "gpt-5.6-luna"

    max_retries: int = 2
    codex_timeout_seconds: int = 1800
    test_timeout_seconds: int = 900
    codex_command: str | None = None
    state_db: Path = Path.home() / ".specialist-agent-workflow" / "checkpoints.sqlite"
    worktree_home: Path = (
        Path("C:/agent-runtime/worktrees")
        if os.name == "nt"
        else Path.home() / ".specialist-agent-workflow" / "worktrees"
    )
    codex_home: Path = (
        Path("C:/agent-runtime/codex-home")
        if os.name == "nt"
        else Path.home() / ".specialist-agent-workflow" / "codex-home"
    )

    def resolved_api_key(self) -> str:
        if self.ai_api_key:
            return self.ai_api_key.get_secret_value()
        for name in ("OPENAI_API_KEY", "NECO_API_KEY"):
            if value := os.getenv(name):
                return value
        auth_path = Path.home() / ".codex" / "auth.json"
        if auth_path.exists():
            payload = json.loads(auth_path.read_text(encoding="utf-8"))
            if value := payload.get("OPENAI_API_KEY"):
                return str(value)
        raise RuntimeError(
            "No API key found. Set AI_API_KEY in .env or sign in with Codex CLI."
        )

    def resolved_base_url(self) -> str:
        if self.ai_base_url:
            return self.ai_base_url.rstrip("/")
        for name in ("OPENAI_BASE_URL", "NECO_BASE_URL"):
            if value := os.getenv(name):
                return value.rstrip("/")
        config_path = Path.home() / ".codex" / "config.toml"
        if config_path.exists():
            payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
            provider_name = payload.get("model_provider")
            providers = payload.get("model_providers", {})
            provider = providers.get(provider_name, {}) if provider_name else {}
            if value := provider.get("base_url"):
                return str(value).rstrip("/")
            if value := payload.get("base_url"):
                return str(value).rstrip("/")
        raise RuntimeError(
            "No Base URL found. Set AI_BASE_URL in .env or configure Codex CLI."
        )

    def resolved_codex_command(self) -> str:
        candidates = [
            self.codex_command,
            shutil.which("codex.cmd"),
            shutil.which("codex"),
            r"C:\nodejs\node_global\codex.cmd",
        ]
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                return str(candidate)
        raise RuntimeError("Codex CLI was not found. Install @openai/codex and fix PATH.")

    def ensure_runtime_directories(self) -> None:
        self.state_db.parent.mkdir(parents=True, exist_ok=True)
        self.worktree_home.mkdir(parents=True, exist_ok=True)
        self.codex_home.mkdir(parents=True, exist_ok=True)

    def public_summary(self) -> dict[str, str]:
        return {
            "base_url": self.resolved_base_url(),
            "planner_model": self.planner_model,
            "planner_api_mode": self.planner_api_mode,
            "code_model": self.code_model,
            "code_reasoning_effort": self.code_reasoning_effort,
            "docs_model": self.docs_model,
            "docs_api_mode": self.docs_api_mode,
            "review_model": self.review_model,
            "review_api_mode": self.review_api_mode,
            "codex_command": self.resolved_codex_command(),
            "worktree_home": str(self.worktree_home),
            "codex_home": str(self.codex_home),
        }
