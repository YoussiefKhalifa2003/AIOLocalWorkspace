"""Preflight checks for the AIO CLI.

Reports presence and reachability only - never prints secret values.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import get_settings


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    hint: str = ""


def _binary_check(name: str, binary: str, version_args: list[str], hint: str) -> Check:
    path = shutil.which(binary)
    if not path:
        return Check(name, False, f"{binary} not on PATH", hint)
    try:
        # Use resolved path so Windows npm .cmd shims are executable.
        proc = subprocess.run(
            [path, *version_args],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return Check(name, False, f"{binary} failed to run ({exc})", hint)
    out = (proc.stdout or proc.stderr or "").strip().splitlines()
    version = out[0] if out else "installed"
    if proc.returncode != 0 and not version:
        return Check(name, False, f"{binary} exited {proc.returncode}", hint)
    return Check(name, True, version[:80])


def check_api() -> Check:
    settings = get_settings()
    url = settings.api_base_url.rstrip("/")
    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.get(f"{url}/health")
        if r.status_code >= 400:
            return Check("API", False, f"{url} returned {r.status_code}", "start uvicorn app.main:app")
        return Check("API", True, f"{url} ({r.json().get('status', 'ok')})")
    except httpx.HTTPError as exc:
        return Check("API", False, f"{url} unreachable ({exc.__class__.__name__})", "start uvicorn app.main:app")


def check_git() -> Check:
    return _binary_check("git", "git", ["--version"], "install git")


def check_codex() -> Check:
    settings = get_settings()
    return _binary_check(
        "Codex CLI",
        settings.codex_bin,
        ["--version"],
        "npm i -g @openai/codex (optional; board runner)",
    )


def check_claude() -> Check:
    settings = get_settings()
    return _binary_check(
        "Claude Code CLI",
        settings.claude_bin,
        ["--version"],
        "npm i -g @anthropic-ai/claude-code (optional; board runner)",
    )


def check_github_token() -> Check:
    settings = get_settings()
    if not (settings.github_token or "").strip():
        return Check("GITHUB_TOKEN", False, "not set", "PRs and merges are disabled without it")
    repo = (settings.github_repo or "").strip() or "(per-project)"
    return Check("GITHUB_TOKEN", True, f"set, repo={repo}")


def check_tavily() -> Check:
    settings = get_settings()
    if not (settings.tavily_api_key or "").strip():
        return Check(
            "TAVILY_API_KEY",
            False,
            "not set",
            "/deepresearch will run without citations",
        )
    return Check("TAVILY_API_KEY", True, "set")


def check_work_root() -> Check:
    settings = get_settings()
    root = Path(settings.agent_work_root)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return Check("AGENT_WORK_ROOT", False, f"{root} not creatable ({exc})")
    if not os.access(root, os.W_OK):
        return Check("AGENT_WORK_ROOT", False, f"{root} not writable")
    return Check("AGENT_WORK_ROOT", True, str(root.resolve()))


def check_coding_backend() -> Check:
    settings = get_settings()
    name = (settings.coding_backend or "llm").lower()
    valid = {"llm", "opencode", "codex", "claude_code"}
    if name not in valid:
        return Check(
            "CODING_BACKEND",
            False,
            f"unknown value {name!r}",
            f"use one of: {', '.join(sorted(valid))}",
        )
    if name == "codex" and not shutil.which(settings.codex_bin):
        return Check(
            "CODING_BACKEND",
            False,
            "codex selected but binary missing",
            "install Codex or keep CODING_BACKEND=llm (use board runner for Codex)",
        )
    if name == "claude_code" and not shutil.which(settings.claude_bin):
        return Check(
            "CODING_BACKEND",
            False,
            "claude_code selected but binary missing",
            "install Claude Code or keep CODING_BACKEND=llm (use board runner for Claude)",
        )
    return Check("CODING_BACKEND", True, name)


def check_outlook_invite() -> Check:
    settings = get_settings()
    domain = (settings.invite_allowed_domain or "").strip() or "(unrestricted)"
    if not settings.outlook_invite_enabled:
        return Check("Outlook invite", True, f"disabled · domain={domain}")
    try:
        import playwright  # noqa: F401
    except ImportError:
        return Check(
            "Outlook invite",
            False,
            f"playwright missing · domain={domain}",
            "pip install playwright && playwright install chromium",
        )
    from app.services.outlook_invite import outlook_storage_path

    storage = outlook_storage_path()
    if not storage.is_file():
        return Check(
            "Outlook invite",
            False,
            f"no session · domain={domain}",
            "run: aio outlook-login",
        )
    return Check("Outlook invite", True, f"ready · domain={domain} · {storage}")


def check_invite_app_url() -> Check:
    """Warn when invite links cannot work off-LAN (empty or private host)."""
    from urllib.parse import urlparse

    from app.services.workspace_invite import invite_public_base_url

    settings = get_settings()
    configured = (settings.invite_app_url or "").strip()
    base = invite_public_base_url()
    host = (urlparse(base).hostname or "").lower()

    if not configured:
        return Check(
            "INVITE_APP_URL",
            False,
            f"not set (using {base})",
            "set INVITE_APP_URL to a public tunnel HTTPS origin for off-LAN signup",
        )

    private = (
        host in {"localhost", "127.0.0.1", "::1"}
        or host.startswith("10.")
        or host.startswith("192.168.")
        or host.startswith("172.")
    )
    if private:
        return Check(
            "INVITE_APP_URL",
            False,
            f"{base} looks LAN/local-only",
            "for off-LAN demos use cloudflared tunnel --url http://127.0.0.1:8000 then remint !invite",
        )

    try:
        with httpx.Client(timeout=5.0, follow_redirects=True) as client:
            r = client.get(f"{base.rstrip('/')}/health")
        if r.status_code >= 400:
            return Check(
                "INVITE_APP_URL",
                False,
                f"{base} returned {r.status_code}",
                "start uvicorn + tunnel, then remint invite",
            )
        return Check("INVITE_APP_URL", True, base)
    except httpx.HTTPError as exc:
        return Check(
            "INVITE_APP_URL",
            False,
            f"{base} unreachable ({exc.__class__.__name__})",
            "start uvicorn + cloudflared; remint !invite after URL changes",
        )


def check_coding_keys() -> Check:
    """Warn when CLI runners are installed but API keys are missing."""
    settings = get_settings()
    missing: list[str] = []
    if shutil.which(settings.codex_bin) and not (settings.codex_api_key or "").strip():
        missing.append("CODEX_API_KEY")
    if shutil.which(settings.claude_bin) and not (settings.anthropic_api_key or "").strip():
        missing.append("ANTHROPIC_API_KEY")
    if not missing:
        if not shutil.which(settings.codex_bin) and not shutil.which(settings.claude_bin):
            return Check(
                "coding keys",
                True,
                "no Codex/Claude CLIs on PATH (optional)",
            )
        return Check("coding keys", True, "set for installed CLIs")
    return Check(
        "coding keys",
        False,
        f"missing {', '.join(missing)}",
        "board runners will fail loudly until keys are set in .env",
    )


def run_checks() -> list[Check]:
    return [
        check_api(),
        check_invite_app_url(),
        check_git(),
        check_work_root(),
        check_github_token(),
        check_tavily(),
        check_codex(),
        check_claude(),
        check_coding_keys(),
        check_coding_backend(),
        check_outlook_invite(),
    ]


def available_coding_runners() -> list[str]:
    """Runners that can actually execute right now (llm always can)."""
    settings = get_settings()
    runners = ["llm"]
    if shutil.which(settings.codex_bin):
        runners.append("codex")
    if shutil.which(settings.claude_bin):
        runners.append("claude_code")
    if (settings.opencode_api_key or "").strip() or shutil.which(settings.opencode_bin):
        runners.append("opencode")
    return runners
