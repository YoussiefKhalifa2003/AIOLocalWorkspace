from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import AgentMetric, Job
from app.services.llm import LLMClient, LLMError


@dataclass
class CodingResult:
    content: str
    backend: str
    model: str | None
    duration_ms: int
    success: bool


class CodingBackend:
    def run(self, *, prompt: str, model: str, llm: LLMClient) -> CodingResult:
        raise NotImplementedError


class LlmCodingBackend(CodingBackend):
    def run(self, *, prompt: str, model: str, llm: LLMClient) -> CodingResult:
        t0 = time.monotonic()
        content = llm.chat(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a coding agent. Write correct, minimal source code for the request. "
                        "Prefer a single fenced code block. Brief notes only if needed. "
                        "Do not write marketing prose or essays."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        )
        ms = int((time.monotonic() - t0) * 1000)
        return CodingResult(content=content, backend="llm", model=model, duration_ms=ms, success=True)


class OpenCodeBackend(CodingBackend):
    def run(self, *, prompt: str, model: str, llm: LLMClient) -> CodingResult:
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


def get_coding_backend() -> CodingBackend:
    settings = get_settings()
    if (settings.coding_backend or "llm").lower() == "opencode":
        return OpenCodeBackend()
    return LlmCodingBackend()


def record_metric(
    db: Session,
    *,
    job: Job,
    backend: str,
    model: str | None,
    success: bool,
    duration_ms: int | None = None,
    tokens: int | None = None,
) -> AgentMetric:
    row = AgentMetric(
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        job_id=job.id,
        backend=backend,
        model=model,
        success=success,
        duration_ms=duration_ms,
        tokens=tokens,
    )
    db.add(row)
    db.flush()
    return row
