from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import AgentMetric, Job
from app.services.llm import LLMClient, LLMError

# Backends that edit files in the objective workspace instead of emitting a blob.
WORKSPACE_BACKENDS = ("codex", "claude_code")


def _resolve_bin(name: str) -> str:
    """Resolve CLI on PATH (needed for Windows npm .cmd shims)."""
    return shutil.which(name) or name


@dataclass
class CodingResult:
    content: str
    backend: str
    model: str | None
    duration_ms: int
    success: bool
    workspace_used: bool = False
    changed_files: list[str] = field(default_factory=list)


class CodingBackend:
    name = "base"
    workspace_capable = False

    def run(
        self,
        *,
        prompt: str,
        model: str,
        llm: LLMClient,
        workspace: str | None = None,
    ) -> CodingResult:
        raise NotImplementedError


class LlmCodingBackend(CodingBackend):
    name = "llm"

    def run(
        self,
        *,
        prompt: str,
        model: str,
        llm: LLMClient,
        workspace: str | None = None,
    ) -> CodingResult:
        t0 = time.monotonic()
        content = llm.chat(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a coding agent. Write correct, minimal source code for the request. "
                        "Prefer a single fenced code block with a language tag. Brief notes only if needed. "
                        "Do not write marketing prose or essays. No HTML."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        )
        ms = int((time.monotonic() - t0) * 1000)
        return CodingResult(content=content, backend="llm", model=model, duration_ms=ms, success=True)


class OpenCodeBackend(CodingBackend):
    name = "opencode"

    def run(
        self,
        *,
        prompt: str,
        model: str,
        llm: LLMClient,
        workspace: str | None = None,
    ) -> CodingResult:
        settings = get_settings()
        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                [settings.opencode_bin, "run", prompt],
                capture_output=True,
                text=True,
                timeout=settings.opencode_timeout_seconds,
                check=False,
            )
            out = (proc.stdout or "").strip() or (proc.stderr or "").strip()
            if proc.returncode != 0 and not out:
                raise LLMError(f"opencode exited {proc.returncode}")
            ms = int((time.monotonic() - t0) * 1000)
            return CodingResult(
                content=out or "(empty opencode output)",
                backend="opencode",
                model=model,
                duration_ms=ms,
                success=proc.returncode == 0,
            )
        except FileNotFoundError as exc:
            raise LLMError("OpenCode binary not found; set CODING_BACKEND=llm") from exc
        except subprocess.TimeoutExpired as exc:
            raise LLMError("OpenCode timed out") from exc


class CodexBackend(CodingBackend):
    """OpenAI Codex CLI in headless mode, editing files in the workspace."""

    name = "codex"
    workspace_capable = True

    def run(
        self,
        *,
        prompt: str,
        model: str,
        llm: LLMClient,
        workspace: str | None = None,
    ) -> CodingResult:
        settings = get_settings()
        t0 = time.monotonic()
        out_path = Path(tempfile.mkdtemp(prefix="aio-codex-")) / "last-message.txt"
        sandbox = (settings.codex_sandbox or "workspace-write").strip().lower()
        argv = [
            _resolve_bin(settings.codex_bin),
            "exec",
            "--skip-git-repo-check",
            "-o",
            str(out_path),
            prompt,
        ]
        # Codex CLI: --sandbox cannot combine with --approve-for-me.
        if sandbox in {"danger-full-access", "danger"}:
            argv[2:2] = ["--dangerously-bypass-approvals-and-sandbox"]
        elif sandbox == "workspace-write":
            argv[2:2] = ["--approve-for-me"]
        else:
            argv[2:2] = ["--sandbox", sandbox]
        env = _agent_env(
            {
                "CODEX_API_KEY": settings.codex_api_key,
                "OPENAI_API_KEY": settings.codex_api_key,
            }
        )
        try:
            proc = subprocess.run(
                argv,
                cwd=workspace or None,
                capture_output=True,
                text=True,
                timeout=settings.codex_timeout_seconds,
                check=False,
                env=env,
            )
        except FileNotFoundError as exc:
            raise LLMError(
                "codex CLI not found; install it (npm i -g @openai/codex) "
                "or set CODING_BACKEND=llm"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise LLMError(
                f"codex timed out after {settings.codex_timeout_seconds}s"
            ) from exc

        final = ""
        try:
            final = out_path.read_text(encoding="utf-8").strip()
        except OSError:
            final = ""
        content = final or (proc.stdout or "").strip() or (proc.stderr or "").strip()
        ms = int((time.monotonic() - t0) * 1000)
        if proc.returncode != 0 and not content:
            raise LLMError(f"codex exited {proc.returncode}")
        return CodingResult(
            content=_scrub(content) or "(codex produced no message)",
            backend="codex",
            model=model,
            duration_ms=ms,
            success=proc.returncode == 0,
            workspace_used=bool(workspace),
        )


class ClaudeCodeBackend(CodingBackend):
    """Anthropic Claude Code CLI in headless mode, editing files in the workspace."""

    name = "claude_code"
    workspace_capable = True

    def run(
        self,
        *,
        prompt: str,
        model: str,
        llm: LLMClient,
        workspace: str | None = None,
    ) -> CodingResult:
        settings = get_settings()
        t0 = time.monotonic()
        argv = [
            _resolve_bin(settings.claude_bin),
            "-p",
            prompt,
            "--permission-mode",
            settings.claude_permission_mode,
            "--output-format",
            "json",
        ]
        env = _agent_env({"ANTHROPIC_API_KEY": settings.anthropic_api_key})
        try:
            proc = subprocess.run(
                argv,
                cwd=workspace or None,
                capture_output=True,
                text=True,
                timeout=settings.claude_timeout_seconds,
                check=False,
                env=env,
            )
        except FileNotFoundError as exc:
            raise LLMError(
                "claude CLI not found; install it (npm i -g @anthropic-ai/claude-code) "
                "or set CODING_BACKEND=llm"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise LLMError(
                f"claude timed out after {settings.claude_timeout_seconds}s"
            ) from exc

        raw = (proc.stdout or "").strip()
        content = raw
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                content = str(data.get("result") or raw)
            elif isinstance(data, list) and data:
                last = data[-1]
                content = str(last.get("result") or raw) if isinstance(last, dict) else raw
        except ValueError:
            content = raw
        if not content:
            content = (proc.stderr or "").strip()
        ms = int((time.monotonic() - t0) * 1000)
        if proc.returncode != 0 and not content:
            raise LLMError(f"claude exited {proc.returncode}")
        return CodingResult(
            content=_scrub(content) or "(claude produced no message)",
            backend="claude_code",
            model=model,
            duration_ms=ms,
            success=proc.returncode == 0,
            workspace_used=bool(workspace),
        )


def _agent_env(extra: dict[str, str]) -> dict[str, str]:
    """Inherit the environment and add credentials - never pass tokens in argv."""
    env = dict(os.environ)
    for key, value in extra.items():
        if (value or "").strip():
            env[key] = value.strip()
    return env


def _scrub(text: str) -> str:
    from app.services.agent_workspace import scrub_secrets

    settings = get_settings()
    out = text or ""
    for token in (
        settings.github_token,
        settings.codex_api_key,
        settings.anthropic_api_key,
        settings.gemini_api_key,
        settings.openrouter_api_key,
    ):
        if (token or "").strip():
            out = out.replace(token.strip(), "***")
    return scrub_secrets(out)


_BACKENDS: dict[str, type[CodingBackend]] = {
    "llm": LlmCodingBackend,
    "opencode": OpenCodeBackend,
    "codex": CodexBackend,
    "claude_code": ClaudeCodeBackend,
}

CODING_RUNNERS = tuple(_BACKENDS)


def get_coding_backend_for(name: str | None) -> CodingBackend:
    """Explicit backend by name; anything unknown falls back to the LLM path."""
    cls = _BACKENDS.get((name or "").strip().lower(), LlmCodingBackend)
    return cls()


def get_coding_backend() -> CodingBackend:
    return get_coding_backend_for(get_settings().coding_backend)


def record_metric(
    db: Session,
    *,
    job: Job,
    backend: str,
    model: str | None,
    success: bool,
    duration_ms: int | None = None,
    tokens: int | None = None,
    user_id: int | None = None,
) -> AgentMetric:
    uid = user_id
    if uid is None and job.request_id:
        from app.db.models import WorkRequest

        req = db.query(WorkRequest).filter(WorkRequest.id == job.request_id).one_or_none()
        if req is not None:
            uid = req.user_id
    row = AgentMetric(
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        job_id=job.id,
        backend=backend,
        model=model,
        success=success,
        duration_ms=duration_ms,
        tokens=tokens,
        user_id=uid,
    )
    db.add(row)
    db.flush()
    return row
