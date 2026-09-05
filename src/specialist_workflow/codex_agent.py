from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from .config import Settings
from .git_ops import changed_files, discard_documentation_changes


class CodexAgent:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def implement(
        self,
        *,
        worktree: Path,
        request: str,
        plan: dict,
        test_command: str,
        feedback: str = "",
    ) -> dict:
        prompt = self._prompt(request, plan, test_command, feedback)
        self._prepare_isolated_codex_home(worktree)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", encoding="utf-8", delete=False
        ) as output_file:
            output_path = Path(output_file.name)
        args = [
            self.settings.resolved_codex_command(),
            "exec",
            "--cd",
            str(worktree),
            "--sandbox",
            "workspace-write",
            "--ephemeral",
            "--color",
            "never",
            "--model",
            self.settings.code_model,
            "--output-last-message",
            str(output_path),
            "-",
        ]
        process_env = os.environ.copy()
        process_env["CODEX_HOME"] = str(self.settings.codex_home.resolve())
        process_env["OPENAI_API_KEY"] = self.settings.resolved_api_key()
        try:
            result = subprocess.run(
                args,
                cwd=worktree,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=self.settings.codex_timeout_seconds,
                shell=False,
                env=process_env,
                input=prompt,
            )
            message = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(
                f"Codex timed out after {self.settings.codex_timeout_seconds} seconds"
            ) from error
        finally:
            output_path.unlink(missing_ok=True)

        if result.returncode != 0:
            detail = (result.stderr or result.stdout or message).strip()
            raise RuntimeError(f"Codex failed with exit code {result.returncode}: {detail[-4000:]}")
        discarded_docs = discard_documentation_changes(worktree)
        code_changes = changed_files(worktree)
        if not code_changes:
            raise RuntimeError(
                "Codex completed without changing any files. The implementation was not applied. "
                f"Last message: {message[-2000:]}\nOutput: {result.stdout[-2000:]}"
            )
        return {
            "message": message.strip(),
            "changed_files": code_changes,
            "discarded_documentation_changes": discarded_docs,
            "stdout_tail": result.stdout[-4000:],
        }

    def _prepare_isolated_codex_home(self, worktree: Path) -> None:
        home = self.settings.codex_home.resolve()
        home.mkdir(parents=True, exist_ok=True)
        uses_codex_auth = self._link_existing_codex_auth(home)
        base_url = json.dumps(self.settings.resolved_base_url())
        model = json.dumps(self.settings.code_model)
        reasoning_effort = json.dumps(self.settings.code_reasoning_effort)
        trusted_path = str(worktree.resolve()).lower().replace("\\", "/")
        trusted_path = json.dumps(trusted_path)
        provider_auth = (
            'requires_openai_auth = true'
            if uses_codex_auth
            else 'env_key = "OPENAI_API_KEY"\nrequires_openai_auth = false'
        )
        config = f"""model_provider = "codex"
model = {model}
model_reasoning_effort = {reasoning_effort}
disable_response_storage = true

[model_providers.codex]
name = "codex"
base_url = {base_url}
wire_api = "responses"
{provider_auth}

[windows]
sandbox = "unelevated"

[projects.{trusted_path}]
trust_level = "trusted"
"""
        config_path = home / "config.toml"
        if not config_path.exists() or config_path.read_text(encoding="utf-8") != config:
            config_path.write_text(config, encoding="utf-8")

    @staticmethod
    def _link_existing_codex_auth(home: Path) -> bool:
        source = (Path.home() / ".codex" / "auth.json").resolve()
        target = home / "auth.json"
        if not source.exists():
            return False
        if target.exists():
            try:
                if os.path.samefile(source, target):
                    return True
            except OSError:
                pass
            target.unlink()
        os.link(source, target)
        return True

    @staticmethod
    def _prompt(request: str, plan: dict, test_command: str, feedback: str) -> str:
        feedback_block = feedback or "No previous review feedback."
        return f"""You are the code implementation specialist in a guarded multi-agent workflow.

User request:
{request}

Approved implementation plan:
{json.dumps(plan, ensure_ascii=False, indent=2)}

Validation command:
{test_command or "Auto-detect the repository's normal test command."}

Feedback from the previous attempt:
{feedback_block}

Implement the request completely in this Git worktree. Inspect the repository before editing.
You may edit source code, tests, build files, and configuration needed for the implementation.
Do not edit README files, changelogs, Markdown, MDX, RST, or files under docs/; a separate
documentation specialist owns those files. Do not commit, merge, push, delete branches, or alter
anything outside this worktree. Run focused tests when practical and finish with a concise account
of changed files and verification performed.
"""
