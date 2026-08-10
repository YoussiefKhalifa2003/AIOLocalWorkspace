from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any, Optional

import httpx
import typer

from app import __version__
from app.config import get_settings
from app.db.session import init_db
from app.services.seed import seed_demo_data
from app.worker import drain_queue, run_worker

app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    help="AIO - the workspace in your terminal. Run bare `aio` to open the app.",
)
jobs_app = typer.Typer(help="Jobs")
rooms_app = typer.Typer(help="Rooms")
review_app = typer.Typer(help="Reviews")
objectives_app = typer.Typer(help="Objectives + progress")
checklist_app = typer.Typer(help="Checklist")
projects_app = typer.Typer(help="Projects")
app.add_typer(jobs_app, name="jobs")
app.add_typer(rooms_app, name="rooms")
app.add_typer(review_app, name="review")
app.add_typer(objectives_app, name="objectives")
app.add_typer(checklist_app, name="checklist")
app.add_typer(projects_app, name="projects")


@app.callback()
def root(ctx: typer.Context) -> None:
    """Bare `aio` opens the full-screen app; subcommands stay scriptable."""
    if ctx.invoked_subcommand is None:
        _launch_app(DEFAULT_PROJECT, None, None, None)


def _headers(api_key: str | None = None, email: str | None = None) -> dict[str, str]:
    from app.cli_pkg.session import auth_headers

    return auth_headers(api_key, email)


def _client(timeout: float = 60.0) -> httpx.Client:
    from app.cli_pkg.session import resolve_base_url

    return httpx.Client(base_url=resolve_base_url(), timeout=timeout)


def _default_project_id() -> int:
    """Stored project from `aio projects use`, resolved once per CLI invocation."""
    from app.cli_pkg.session import load_credentials

    return load_credentials().project_id or 1


DEFAULT_PROJECT = _default_project_id()


def _detail(response: httpx.Response) -> str:
    try:
        return str(response.json().get("detail") or response.text)
    except ValueError:
        return response.text


def _fetch_board(project_id: int, api_key: str | None = None) -> dict[str, Any]:
    with _client() as client:
        r = client.get(f"/projects/{project_id}/board", headers=_headers(api_key))
    if r.status_code >= 400:
        typer.echo(_detail(r))
        raise typer.Exit(1)
    return r.json()


def _fetch_card(project_id: int, objective_id: int, api_key: str | None = None) -> dict[str, Any]:
    board = _fetch_board(project_id, api_key)
    card = next(
        (
            c
            for col in board.get("columns", [])
            for c in col.get("cards", [])
            if c["id"] == objective_id
        ),
        None,
    )
    if card is None:
        typer.echo(f"objective #{objective_id} not found in project {project_id}")
        raise typer.Exit(1)
    return card


def _print_table(rows: list[dict[str, Any]], keys: list[str]) -> None:
    if not rows:
        typer.echo("(empty)")
        return
    widths = {k: max(len(k), *(len(str(r.get(k, ""))) for r in rows)) for k in keys}
    header = "  ".join(k.ljust(widths[k]) for k in keys)
    typer.echo(header)
    typer.echo("  ".join("-" * widths[k] for k in keys))
    for r in rows:
        typer.echo("  ".join(str(r.get(k, "")).ljust(widths[k]) for k in keys))


def _print_objectives(data: dict[str, Any]) -> None:
    typer.echo(f"OBJECTIVES  {data['bar']}  {data['done']}/{data['total']}")
    if not data["objectives"]:
        typer.echo("(no objectives yet - add with: ./aio objectives add \"...\")")
        return
    for obj in data["objectives"]:
        mark = "x" if obj["done"] else " "
        req = f"  req={obj['request_id']}" if obj.get("request_id") else ""
        typer.echo(f"[{mark}] {obj['id']}. {obj['title']}{req}")


def _print_checklist(items: list[dict[str, Any]]) -> None:
    total = len(items)
    done = sum(1 for i in items if i.get("done"))
    width = 20
    filled = int(round(width * done / total)) if total else 0
    bar = f"[{'#' * filled}{'-' * (width - filled)}] {int(round(100 * done / total)) if total else 0}%"
    typer.echo(f"CHECKLIST  {bar}  {done}/{total}")
    if not items:
        typer.echo("(empty - run an objective that creates follow-up tasks)")
        return
    for item in items:
        mark = "x" if item["done"] else " "
        typer.echo(f"[{mark}] {item['id']}. {item['title']}")


@app.command()
def version() -> None:
    typer.echo(__version__)


@app.command()
def health() -> None:
    with _client() as client:
        r = client.get("/health")
        r.raise_for_status()
        data = r.json()
    typer.echo(f"status={data.get('status')} version={data.get('version')}")


@app.command()
def login(
    email: str = typer.Option(..., "--email", "-e", prompt=True),
    password: str = typer.Option(..., "--password", "-p", prompt=True, hide_input=True),
    project_id: Optional[int] = typer.Option(None, "--project-id"),
) -> None:
    """Sign in and store credentials in ~/.aio/credentials.json (mode 600)."""
    from app.cli_pkg.session import Credentials, resolve_base_url, save_credentials

    base = resolve_base_url()
    with httpx.Client(base_url=base, timeout=30.0) as client:
        r = client.post("/auth/login", json={"email": email, "password": password})
        if r.status_code >= 400:
            typer.echo("login failed: invalid email or password")
            raise typer.Exit(1)
        data = r.json()
        creds = Credentials(
            api_key=data["api_key"],
            email=data["email"],
            user_id=int(data["user_id"]),
            project_id=int(project_id or 0),
            api_base_url=base,
        )
        me = client.get("/auth/me", headers=_headers(creds.api_key, creds.email)).json()

    path = save_credentials(creds)
    role = "owner" if me.get("is_owner") else "member"
    typer.echo(f"logged in as {creds.email} ({role}, user_id={creds.user_id})")
    typer.echo(f"credentials: {path}")
    if not creds.project_id:
        typer.echo("tip: set a default project with `aio projects use <id>`")


@app.command()
def logout() -> None:
    """Remove stored credentials."""
    from app.cli_pkg.session import clear_credentials, credentials_path

    if clear_credentials():
        typer.echo(f"removed {credentials_path()}")
    else:
        typer.echo("not logged in")


@app.command()
def whoami() -> None:
    """Show the stored identity and whether it owns the workspace."""
    from app.cli_pkg.session import load_credentials, resolve_base_url

    creds = load_credentials()
    if creds.is_empty():
        typer.echo("not logged in (run: aio login --email <you>)")
        raise typer.Exit(1)
    with _client(timeout=15.0) as client:
        r = client.get("/auth/me", headers=_headers())
    if r.status_code >= 400:
        typer.echo(f"stored credentials rejected by {resolve_base_url()} ({r.status_code})")
        raise typer.Exit(1)
    me = r.json()
    typer.echo(f"email:   {me.get('email')}")
    typer.echo(f"user_id: {me.get('user_id')}")
    typer.echo(f"owner:   {bool(me.get('is_owner'))}")
    typer.echo(f"api:     {resolve_base_url()}")
    typer.echo(f"project: {creds.project_id or DEFAULT_PROJECT}")


@app.command()
def doctor() -> None:
    """Preflight: API, git, workspaces, GitHub, research, coding runners."""
    from app.cli_pkg.doctor import available_coding_runners, run_checks

    checks = run_checks()
    width = max(len(c.name) for c in checks)
    failures = 0
    for c in checks:
        mark = typer.style("OK  ", fg=typer.colors.GREEN) if c.ok else typer.style("FAIL", fg=typer.colors.RED)
        line = f"{mark} {c.name.ljust(width)}  {c.detail}"
        typer.echo(line)
        if not c.ok:
            failures += 1
            if c.hint:
                typer.echo(f"     {' ' * width}  -> {c.hint}")
    typer.echo("")
    typer.echo(f"coding runners available: {', '.join(available_coding_runners())}")
    if failures:
        typer.echo(f"{failures} check(s) need attention")


@app.command("outlook-login")
def outlook_login() -> None:
    """One-time Outlook Web sign-in for free invite emails (Playwright, no SMTP billing)."""
    from app.services.outlook_invite import interactive_outlook_login, outlook_storage_path

    typer.echo("Opening Outlook in Chromium…")
    typer.echo("(Ignore Homebrew playwright - AIO uses .venv/bin/python -m playwright)")
    typer.echo("Correct command is:  ./aio outlook-login")
    typer.echo("Not:                 ./aio run outlook-login")
    try:
        path = interactive_outlook_login(headed=True)
    except Exception as exc:  # noqa: BLE001
        typer.secho(str(exc), fg=typer.colors.RED)
        typer.echo("If Chromium is missing, run:")
        typer.echo("  .venv/bin/python -m playwright install chromium")
        raise typer.Exit(1) from exc
    saved = path or outlook_storage_path()
    typer.echo(f"Saved session to {saved}")
    if not Path(saved).is_file():
        typer.secho("Session file missing after login — try again.", fg=typer.colors.RED)
        raise typer.Exit(1)
    typer.echo("Invites: ./aio invite-email colleague@example.com  or  !invite email@domain")


@app.command("invite-email")
def invite_email_cmd(
    email: str = typer.Argument(..., help="Colleague email to invite"),
    seats: int = typer.Option(1, "--seats", "-n", help="Invite link uses (1-50)"),
) -> None:
    """Mint an invite link and email it via Outlook (visible Chromium window)."""
    from app.cli_pkg.session import load_credentials
    from app.services.invite_domain import assert_allowed_invite_email
    from app.services.outlook_invite import send_invite_via_outlook

    try:
        to = assert_allowed_invite_email(email)
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(1) from exc

    creds = load_credentials()
    if not creds.api_key:
        typer.secho("Not logged in. Run: aio login", fg=typer.colors.RED)
        raise typer.Exit(1)

    typer.echo(f"Minting invite link ({seats} seat(s))…")
    with _client(timeout=30.0) as client:
        r = client.post(
            "/workspace/invite-link",
            headers=_headers(creds.api_key, creds.email),
            params={"max_uses": max(1, min(50, seats))},
        )
    if r.status_code >= 400:
        typer.secho(f"API mint failed: {_detail(r)}", fg=typer.colors.RED)
        raise typer.Exit(1)

    url = str((r.json() or {}).get("invite_url") or "")
    if not url:
        typer.secho("No invite_url from API", fg=typer.colors.RED)
        raise typer.Exit(1)

    typer.echo(f"Link: {url}")
    typer.echo(f"Opening Outlook to email {to} (watch the Chromium window)…")
    result = send_invite_via_outlook(
        to_email=to,
        invite_url=url,
        max_uses=seats,
        workspace="AIO",
        headless=False,
    )
    if result.get("ok"):
        typer.secho(f"Sent to {to}", fg=typer.colors.GREEN)
    else:
        typer.secho(f"Email failed: {result.get('reason')}", fg=typer.colors.RED)
        typer.echo("Link is still valid - share it manually if needed.")
        raise typer.Exit(1)


@app.command()
def seed() -> None:
    init_db()
    info = seed_demo_data()
    typer.echo("seeded:")
    for k, v in info.items():
        typer.echo(f"  {k}={v}")


@projects_app.callback(invoke_without_command=True)
def projects_root(ctx: typer.Context) -> None:
    """List projects (or use a subcommand)."""
    if ctx.invoked_subcommand is None:
        _projects_table(None)


@projects_app.command("list")
def projects_list_cmd(api_key: Optional[str] = typer.Option(None, "--api-key")) -> None:
    _projects_table(api_key)


@projects_app.command("use")
def projects_use(project_id: int = typer.Argument(...)) -> None:
    """Set the default project for later commands."""
    from app.cli_pkg.session import load_credentials, save_credentials

    creds = load_credentials()
    if creds.is_empty():
        typer.echo("not logged in (run: aio login --email <you>)")
        raise typer.Exit(1)
    creds.project_id = int(project_id)
    save_credentials(creds)
    typer.echo(f"default project set to {project_id}")


def _projects_table(api_key: str | None) -> None:
    with _client() as client:
        r = client.get("/projects", headers=_headers(api_key))
    if r.status_code >= 400:
        typer.echo(_detail(r))
        raise typer.Exit(1)
    _print_table(r.json(), ["id", "tenant_id", "name", "github_repo"])


@app.command("board")
def board_show(
    project_id: int = typer.Option(DEFAULT_PROJECT, "--project-id"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
) -> None:
    """Print the board with repo / PR / branch links per card."""
    board = _fetch_board(project_id, api_key)
    repo = board.get("repo_url") or "-"
    typer.echo(f"project={board.get('project_id')} repo={repo}")
    for col in board.get("columns", []):
        cards = col.get("cards", [])
        typer.echo("")
        typer.echo(f"{col['id']} ({len(cards)})")
        if not cards:
            typer.echo("  (empty)")
            continue
        for c in cards:
            pr = c.get("pr_url") or "-"
            branch = c.get("github_branch") or "-"
            typer.echo(f"  #{c['id']} {c['title']} [{c.get('status')}]")
            typer.echo(f"      pr={pr} repo={c.get('repo_url') or '-'} branch={branch}")


@app.command("tui")
def tui(
    project_id: int = typer.Option(DEFAULT_PROJECT, "--project-id"),
    poll: Optional[float] = typer.Option(None, "--poll", help="Refresh interval in seconds"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
    email: Optional[str] = typer.Option(None, "--email"),
) -> None:
    """The whole workspace in your terminal: chat, board, agents, dashboard."""
    _launch_app(project_id, poll, api_key, email)


@app.command("up")
def up(
    project_id: int = typer.Option(DEFAULT_PROJECT, "--project-id"),
    poll: Optional[float] = typer.Option(None, "--poll", help="Refresh interval in seconds"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
    email: Optional[str] = typer.Option(None, "--email"),
) -> None:
    """Alias for `aio tui` - open the terminal app."""
    _launch_app(project_id, poll, api_key, email)


def _launch_app(
    project_id: int, poll: Optional[float], api_key: Optional[str], email: Optional[str]
) -> None:
    from app.cli_pkg.tui.app import run_app

    code = run_app(
        project_id,
        poll_seconds=poll if poll is not None else get_settings().tui_poll_seconds,
        api_key=api_key or "",
        email=email or "",
    )
    if code:
        raise typer.Exit(code)


@app.command("card")
def card_show(
    objective_id: int = typer.Argument(...),
    project_id: int = typer.Option(DEFAULT_PROJECT, "--project-id"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
) -> None:
    """Card detail: subtasks, claims, repo / PR / branch links, workspace path."""
    card = _fetch_card(project_id, objective_id, api_key)
    typer.echo(f"#{card['id']} {card['title']}  [{card.get('status')}]")
    if card.get("description"):
        typer.echo(f"  {card['description']}")
    typer.echo(f"  owner:    {card.get('owner_email') or '-'}")
    typer.echo(
        f"  progress: {card.get('progress_percent', 0)}% "
        f"({card.get('checklist_closed', 0)}/{card.get('checklist_total', 0)} subtasks)"
    )
    typer.echo(f"  blockers: {card.get('open_issue_count', 0)}")
    typer.echo(f"  repo:     {card.get('repo_url') or '-'}")
    typer.echo(f"  pr:       {card.get('pr_url') or '-'}")
    typer.echo(f"  branch:   {card.get('branch_url') or card.get('github_branch') or '-'}")
    typer.echo(f"  merged:   {card.get('github_merged_at') or '-'}")
    typer.echo(f"  workspace: data/workspaces/obj-{card['id']}")
    for t in card.get("subtasks") or []:
        typer.echo(f"    [{'x' if t['done'] else ' '}] {t['title']}")
    for p in card.get("claimed_paths") or []:
        typer.echo(f"    claim: {p}")


@app.command("board-wipe")
def board_wipe(
    project_id: int = typer.Option(DEFAULT_PROJECT, "--project-id"),
    yes: bool = typer.Option(False, "--yes", help="Required confirmation"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
) -> None:
    """Owner-only: delete every board card and local agent workspaces for a project."""
    if not yes:
        typer.echo("refusing: pass --yes to wipe the board")
        raise typer.Exit(2)
    with _client() as client:
        r = client.post(
            f"/projects/{project_id}/board/wipe",
            headers=_headers(api_key),
            json={"confirm": True},
        )
    if r.status_code >= 400:
        typer.echo(_detail(r))
        raise typer.Exit(1)
    data = r.json()
    typer.echo(
        f"wiped objectives={data.get('deleted_objectives')} "
        f"requests={data.get('deleted_requests')} "
        f"workspaces={data.get('removed_workspaces')}"
    )


@app.command("set")
def set_status(
    objective_id: int = typer.Argument(...),
    status: str = typer.Argument(..., help="todo|doing|blocked|agent_backlog|in_review|done"),
    project_id: int = typer.Option(DEFAULT_PROJECT, "--project-id"),
    runner: Optional[str] = typer.Option(
        None, "--runner", help="For agent_backlog: llm | codex | claude_code | opencode"
    ),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
) -> None:
    """Move a card to another board column."""
    payload: dict[str, Any] = {"status": status}
    if runner:
        payload["coding_runner"] = runner
    with _client() as client:
        r = client.patch(
            f"/projects/{project_id}/objectives/{objective_id}",
            headers=_headers(api_key),
            json=payload,
        )
    if r.status_code >= 400:
        typer.echo(_detail(r))
        raise typer.Exit(1)
    out = r.json()
    typer.echo(f"#{out['id']} -> {out['status']}")
    if status == "agent_backlog":
        typer.echo("agent started; watch with `aio board` or `aio tui`")


@app.command("chat")
def chat_read(
    chat_id: int = typer.Argument(...),
    follow: bool = typer.Option(False, "--follow", "-f", help="Poll for new messages"),
    limit: int = typer.Option(30, "--limit"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
) -> None:
    """Read a chat, optionally following new messages."""
    after = 0
    with _client() as client:
        while True:
            r = client.get(
                f"/chats/{chat_id}/messages",
                headers=_headers(api_key),
                params={"after_id": after, "limit": limit},
            )
            if r.status_code >= 400:
                typer.echo(_detail(r))
                raise typer.Exit(1)
            for m in r.json():
                after = max(after, int(m["id"]))
                who = m.get("agent_slug") or m.get("sender_name") or m.get("sender_email") or "?"
                typer.echo(f"[{m['id']}] {who}: {m.get('body') or ''}")
            if not follow:
                return
            time.sleep(2.0)


@app.command("say")
def chat_say(
    chat_id: int = typer.Argument(...),
    text: str = typer.Argument(...),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
) -> None:
    """Post a message and print any agent replies."""
    with _client(timeout=180.0) as client:
        r = client.post(
            f"/chats/{chat_id}/messages",
            headers=_headers(api_key),
            json={"body": text, "speak": False},
        )
    if r.status_code >= 400:
        typer.echo(_detail(r))
        raise typer.Exit(1)
    data = r.json()
    for reply in data.get("replies") or []:
        who = reply.get("agent_slug") or "agent"
        typer.echo(f"{who}: {reply.get('body') or ''}")


@app.command("members")
def members_list(api_key: Optional[str] = typer.Option(None, "--api-key")) -> None:
    """Workspace members and roles."""
    with _client() as client:
        r = client.get("/workspace/members", headers=_headers(api_key))
    if r.status_code >= 400:
        typer.echo(_detail(r))
        raise typer.Exit(1)
    _print_table(r.json(), ["user_id", "name", "email", "role"])


@app.command("workspaces")
def workspaces_list() -> None:
    """Local agent checkouts under AGENT_WORK_ROOT."""
    import subprocess
    from pathlib import Path

    root = Path(get_settings().agent_work_root)
    if not root.exists():
        typer.echo(f"(no workspaces yet under {root})")
        return
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("obj-*")):
        if not (path / ".git").exists():
            rows.append({"workspace": path.name, "branch": "(not a git checkout)", "dirty": "-"})
            continue

        def _git(args: list[str], cwd: Path = path) -> str:
            try:
                p = subprocess.run(
                    ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=15
                )
            except (OSError, subprocess.SubprocessError):
                return ""
            return (p.stdout or "").strip()

        rows.append(
            {
                "workspace": path.name,
                "branch": _git(["rev-parse", "--abbrev-ref", "HEAD"]) or "?",
                "dirty": "yes" if _git(["status", "--porcelain"]) else "no",
            }
        )
    _print_table(rows, ["workspace", "branch", "dirty"])


@app.command("pr")
def pr_show(
    objective_id: int = typer.Argument(...),
    project_id: int = typer.Option(DEFAULT_PROJECT, "--project-id"),
    open_browser: bool = typer.Option(False, "--open", help="Open the PR in a browser"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
) -> None:
    """Print (or open) the PR URL for an objective."""
    card = _fetch_card(project_id, objective_id, api_key)
    url = card.get("pr_url")
    if not url:
        typer.echo(f"#{objective_id} has no pull request yet")
        raise typer.Exit(1)
    typer.echo(url)
    if open_browser:
        import webbrowser

        webbrowser.open(url)


@app.command("merge")
def merge_objective_cmd(
    objective_id: int = typer.Argument(...),
    project_id: int = typer.Option(DEFAULT_PROJECT, "--project-id"),
    method: Optional[str] = typer.Option(None, "--method", help="squash | merge | rebase"),
    keep_branch: bool = typer.Option(False, "--keep-branch"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
) -> None:
    """Merge an in_review objective's PR, then move the card to done."""
    card = _fetch_card(project_id, objective_id, api_key)
    if not card.get("can_merge"):
        typer.echo(
            f"#{objective_id} is not mergeable from AIO "
            f"(status={card.get('status')}, pr={card.get('pr_url') or 'none'})"
        )
        raise typer.Exit(1)

    typer.echo(f"#{card['id']} {card['title']}")
    typer.echo(f"  pr:     {card.get('pr_url')}")
    typer.echo(f"  branch: {card.get('github_branch') or '-'}")
    typer.echo(f"  method: {method or get_settings().merge_method}")
    typer.echo("  warning: merging into the default branch cannot be easily undone")
    if not yes and not typer.confirm("Merge and mark the objective done?"):
        typer.echo("aborted, nothing merged")
        raise typer.Exit(1)

    payload: dict[str, Any] = {"confirm": True, "delete_branch": not keep_branch}
    if method:
        payload["merge_method"] = method
    with _client() as client:
        m = client.post(
            f"/projects/{project_id}/objectives/{objective_id}/merge",
            headers=_headers(api_key),
            json=payload,
        )
    if m.status_code >= 400:
        try:
            detail = m.json().get("detail")
        except ValueError:
            detail = m.text
        typer.echo(f"merge failed: {detail}")
        raise typer.Exit(1)
    out = m.json()
    typer.echo(f"merged into {out.get('base')} sha={out.get('sha')}")
    typer.echo(f"objective #{objective_id} -> done")


@app.command()
def ask(
    text: str = typer.Argument(...),
    project_id: int = typer.Option(DEFAULT_PROJECT, "--project-id"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
    wait: bool = typer.Option(False, "--wait", help="Drain worker queue after ask"),
) -> None:
    with _client() as client:
        r = client.post(
            f"/projects/{project_id}/requests",
            headers=_headers(api_key),
            json={"text": text},
        )
        if r.status_code >= 400:
            typer.echo(r.text)
            raise typer.Exit(1)
        data = r.json()
    typer.echo(f"request_id={data['request_id']}")
    typer.echo(f"agents={','.join(data['agents'])}")
    typer.echo(f"job_ids={','.join(str(i) for i in data['job_ids'])}")
    typer.echo(f"reason={data['reason']} used_llm={data['used_llm']}")
    if wait:
        n = drain_queue()
        typer.echo(f"worker_processed={n}")


@app.command("request")
def request_show(
    request_id: int,
    project_id: int = typer.Option(DEFAULT_PROJECT, "--project-id"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
) -> None:
    with _client() as client:
        r = client.get(
            f"/projects/{project_id}/requests/{request_id}",
            headers=_headers(api_key),
        )
        r.raise_for_status()
        data = r.json()
    typer.echo(f"id={data['id']} status={data['status']}")
    typer.echo(f"pipeline={data['pipeline']}")
    typer.echo(f"text={data['text']}")
    typer.echo("--- jobs ---")
    _print_table(data["jobs"], ["id", "agent_type", "status", "pipeline_index", "model_used"])


@jobs_app.command("list")
def jobs_list(
    project_id: int = typer.Option(DEFAULT_PROJECT, "--project-id"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
) -> None:
    with _client() as client:
        r = client.get(f"/projects/{project_id}/jobs", headers=_headers(api_key))
        r.raise_for_status()
        rows = r.json()
    _print_table(
        rows,
        ["id", "agent_type", "status", "pipeline_index", "model_used", "error"],
    )


@jobs_app.command("show")
def jobs_show(
    job_id: int,
    project_id: int = typer.Option(DEFAULT_PROJECT, "--project-id"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
) -> None:
    with _client() as client:
        r = client.get(f"/projects/{project_id}/jobs/{job_id}", headers=_headers(api_key))
        r.raise_for_status()
        j = r.json()
    for k, v in j.items():
        typer.echo(f"{k}={v}")


@app.command("artifacts")
def artifacts_cmd(
    action: str = typer.Argument("list"),
    artifact_id: Optional[int] = typer.Argument(None),
    project_id: int = typer.Option(DEFAULT_PROJECT, "--project-id"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
) -> None:
    with _client() as client:
        if action == "list":
            r = client.get(f"/projects/{project_id}/artifacts", headers=_headers(api_key))
            r.raise_for_status()
            rows = r.json()
            _print_table(
                [{"id": a["id"], "job_id": a["job_id"], "agent_type": a["agent_type"], "title": a["title"]} for a in rows],
                ["id", "job_id", "agent_type", "title"],
            )
        elif action == "show":
            if artifact_id is None:
                raise typer.Exit(code=1)
            r = client.get(
                f"/projects/{project_id}/artifacts/{artifact_id}",
                headers=_headers(api_key),
            )
            r.raise_for_status()
            a = r.json()
            typer.echo(f"id={a['id']} title={a['title']} agent={a['agent_type']}")
            typer.echo("---")
            typer.echo(a["content"])
        else:
            typer.echo("usage: aio artifacts list|show <id>")
            raise typer.Exit(1)


@app.command()
def tasks(
    project_id: int = typer.Option(DEFAULT_PROJECT, "--project-id"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
) -> None:
    """Alias for checklist list (table view)."""
    with _client() as client:
        r = client.get(f"/projects/{project_id}/tasks", headers=_headers(api_key))
        r.raise_for_status()
        rows = r.json()
    _print_table(rows, ["id", "done", "title", "job_id"])


@objectives_app.callback(invoke_without_command=True)
def objectives_root(
    ctx: typer.Context,
    project_id: int = typer.Option(DEFAULT_PROJECT, "--project-id"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
) -> None:
    """Show objectives + progress bar. Subcommands: add, run, complete, clear."""
    if ctx.invoked_subcommand is not None:
        return
    with _client() as client:
        r = client.get(f"/projects/{project_id}/objectives", headers=_headers(api_key))
        r.raise_for_status()
        _print_objectives(r.json())


@objectives_app.command("add")
def objectives_add(
    title: str = typer.Argument(...),
    project_id: int = typer.Option(DEFAULT_PROJECT, "--project-id"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
) -> None:
    with _client() as client:
        r = client.post(
            f"/projects/{project_id}/objectives",
            headers=_headers(api_key),
            json={"title": title},
        )
        if r.status_code >= 400:
            typer.echo(r.text)
            raise typer.Exit(1)
        obj = r.json()
        typer.echo(f"added id={obj['id']} title={obj['title']}")
        prog = client.get(
            f"/projects/{project_id}/objectives", headers=_headers(api_key)
        ).json()
    _print_objectives(prog)


@objectives_app.command("run")
def objectives_run(
    objective_id: int = typer.Argument(...),
    project_id: int = typer.Option(DEFAULT_PROJECT, "--project-id"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Run worker until jobs finish"),
) -> None:
    """Run one objective through the auto-router (no @mentions)."""
    headers = _headers(api_key)
    with _client() as client:
        r = client.post(
            f"/projects/{project_id}/objectives/{objective_id}/run",
            headers=headers,
        )
        if r.status_code >= 400:
            typer.echo(r.text)
            raise typer.Exit(1)
        data = r.json()
    typer.echo(
        f"running objective={data['objective_id']} request={data['request_id']} "
        f"agents={','.join(data['agents'])}"
    )
    if wait:
        typer.echo("working...")
        n = drain_queue(max_jobs=30)
        typer.echo(f"worker_processed={n}")
        with _client() as client:
            done = client.post(
                f"/projects/{project_id}/objectives/{objective_id}/complete",
                headers=headers,
            )
            if done.status_code >= 400:
                typer.echo(f"not marked done: {done.text}")
            else:
                typer.echo(f"objective {objective_id} COMPLETE")

            # Proof: show what the agents actually produced
            result = client.get(
                f"/projects/{project_id}/requests/{data['request_id']}/result",
                headers=headers,
            )
            if result.status_code < 400:
                body = result.json()
                typer.echo("")
                typer.echo("=== RESULT (proof of work) ===")
                for job in body.get("jobs", []):
                    typer.echo(f"job {job['id']}: {job['agent_type']} -> {job['status']}")
                for art in body.get("artifacts", []):
                    typer.echo(f"--- {art['agent_type']}: {art['title']} ---")
                    typer.echo(art["content"])
                    typer.echo("")
            else:
                typer.echo("(no result artifacts found)")

            prog = client.get(
                f"/projects/{project_id}/objectives", headers=headers
            ).json()
        _print_objectives(prog)


@objectives_app.command("result")
def objectives_result(
    objective_id: int = typer.Argument(...),
    project_id: int = typer.Option(DEFAULT_PROJECT, "--project-id"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
) -> None:
    """Show proof/output for an objective that already ran."""
    headers = _headers(api_key)
    with _client() as client:
        prog = client.get(f"/projects/{project_id}/objectives", headers=headers).json()
        obj = next((o for o in prog["objectives"] if o["id"] == objective_id), None)
        if obj is None:
            typer.echo("objective not found")
            raise typer.Exit(1)
        if not obj.get("request_id"):
            typer.echo("objective has not been run yet")
            raise typer.Exit(1)
        result = client.get(
            f"/projects/{project_id}/requests/{obj['request_id']}/result",
            headers=headers,
        )
        if result.status_code >= 400:
            typer.echo(result.text)
            raise typer.Exit(1)
        body = result.json()
    typer.echo(f"objective {objective_id}: {obj['title']}")
    typer.echo(f"done={obj['done']} request={obj['request_id']}")
    typer.echo("=== RESULT ===")
    for art in body.get("artifacts", []):
        typer.echo(f"--- {art['agent_type']}: {art['title']} ---")
        typer.echo(art["content"])
        typer.echo("")


@objectives_app.command("complete")
def objectives_complete(
    objective_id: int = typer.Argument(...),
    project_id: int = typer.Option(DEFAULT_PROJECT, "--project-id"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
) -> None:
    with _client() as client:
        r = client.post(
            f"/projects/{project_id}/objectives/{objective_id}/complete",
            headers=_headers(api_key),
        )
        if r.status_code >= 400:
            typer.echo(r.text)
            raise typer.Exit(1)
        prog = client.get(
            f"/projects/{project_id}/objectives", headers=_headers(api_key)
        ).json()
    _print_objectives(prog)


@objectives_app.command("remove")
def objectives_remove(
    objective_id: int = typer.Argument(...),
    project_id: int = typer.Option(DEFAULT_PROJECT, "--project-id"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
) -> None:
    """Remove one objective from the list."""
    with _client() as client:
        r = client.delete(
            f"/projects/{project_id}/objectives/{objective_id}",
            headers=_headers(api_key),
        )
        if r.status_code >= 400:
            typer.echo(r.text)
            raise typer.Exit(1)
        typer.echo(f"removed objective {objective_id}")
        prog = client.get(
            f"/projects/{project_id}/objectives", headers=_headers(api_key)
        ).json()
    _print_objectives(prog)


@objectives_app.command("clear")
def objectives_clear(
    project_id: int = typer.Option(DEFAULT_PROJECT, "--project-id"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
) -> None:
    """Remove ALL objectives for a clean start."""
    headers = _headers(api_key)
    with _client() as client:
        prog = client.get(f"/projects/{project_id}/objectives", headers=headers).json()
        ids = [o["id"] for o in prog.get("objectives", [])]
        for oid in ids:
            r = client.delete(
                f"/projects/{project_id}/objectives/{oid}",
                headers=headers,
            )
            if r.status_code >= 400:
                typer.echo(f"failed to remove {oid}: {r.text}")
                raise typer.Exit(1)
        typer.echo(f"cleared {len(ids)} objectives")
        prog = client.get(f"/projects/{project_id}/objectives", headers=headers).json()
    _print_objectives(prog)


@app.command("host")
def host_guide() -> None:
    """Print host startup steps (API + tunnel + Outlook + CLI)."""
    typer.echo(
        """
AIO host — keep these terminals open

  T1  API
      cd WORK && source .venv/bin/activate
      uvicorn app.main:app --host 0.0.0.0 --port 8000

  T2  Cloudflare (off-LAN invites only)
      cloudflared tunnel --url http://127.0.0.1:8000
      → copy https://….trycloudflare.com into .env as INVITE_APP_URL=…
      → remint with !invite (URL is re-read from .env; restart API if unsure)

  T3  One-time Outlook (invite emails)
      ./aio outlook-login
      (not ./aio run outlook-login)
      → creates data/outlook_auth.json (gitignored)

  T4  Your CLI
      ./aio                 # Windows: .\\aio.cmd
      Sign in → Server http://127.0.0.1:8000 (on this machine)
      Then: !invite colleague@email.com

  Members (other machines)
      Open join link → register → install deps → launch:
        macOS/Linux:  ./aio
        Windows:      .\\aio.cmd
      Paste Server (tunnel HTTPS) + email/password.
      They do NOT run uvicorn or cloudflared.

  Preflight: ./aio doctor   (Windows: .\\aio.cmd doctor)
""".strip()
    )


@app.command()
def run(
    objective_id: str = typer.Argument(..., help="Board objective id (integer)"),
    project_id: int = typer.Option(DEFAULT_PROJECT, "--project-id"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
    wait: bool = typer.Option(True, "--wait/--no-wait"),
) -> None:
    """Shortcut: ./aio run <objective_id>  — board jobs only (not Outlook)."""
    key = objective_id.strip().lower().replace("_", "-")
    if key in {"outlook-login", "outlook", "invite-email", "host", "doctor"}:
        typer.secho(
            f"'./aio run {objective_id}' is not valid.\n"
            f"For Outlook invites use:  ./aio outlook-login\n"
            f"For host steps use:      ./aio host",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)
    try:
        oid = int(objective_id)
    except ValueError as exc:
        typer.secho(
            f"Expected an objective id number, got {objective_id!r}.\n"
            "Board run:   ./aio run 12\n"
            "Outlook:     ./aio outlook-login",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1) from exc
    objectives_run(
        objective_id=oid,
        project_id=project_id,
        api_key=api_key,
        wait=wait,
    )


@checklist_app.callback(invoke_without_command=True)
def checklist_root(
    ctx: typer.Context,
    project_id: int = typer.Option(DEFAULT_PROJECT, "--project-id"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
    all_items: bool = typer.Option(False, "--all", help="Show every old checklist item"),
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    with _client() as client:
        r = client.get(
            f"/projects/{project_id}/checklist",
            headers=_headers(api_key),
            params={"all_items": all_items},
        )
        r.raise_for_status()
        _print_checklist(r.json())
    if not all_items:
        typer.echo("(showing latest batch only - use ./aio checklist --all for history)")


@checklist_app.command("done")
def checklist_mark_done(
    item_id: int = typer.Argument(...),
    project_id: int = typer.Option(DEFAULT_PROJECT, "--project-id"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
    undo: bool = typer.Option(False, "--undo"),
) -> None:
    with _client() as client:
        r = client.post(
            f"/projects/{project_id}/checklist/{item_id}/done",
            headers=_headers(api_key),
            params={"done": (not undo)},
        )
        if r.status_code >= 400:
            typer.echo(r.text)
            raise typer.Exit(1)
        items = client.get(
            f"/projects/{project_id}/checklist", headers=_headers(api_key)
        ).json()
    _print_checklist(items)


@checklist_app.command("clear")
def checklist_clear(
    project_id: int = typer.Option(DEFAULT_PROJECT, "--project-id"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
) -> None:
    """Delete all checklist items for a clean demo."""
    with _client() as client:
        r = client.delete(
            f"/projects/{project_id}/checklist",
            headers=_headers(api_key),
        )
        if r.status_code >= 400:
            typer.echo(r.text)
            raise typer.Exit(1)
        typer.echo(f"cleared {r.json().get('deleted', 0)} items")


@app.command()
def audit(
    project_id: int = typer.Option(DEFAULT_PROJECT, "--project-id"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
) -> None:
    with _client() as client:
        r = client.get(f"/projects/{project_id}/audit", headers=_headers(api_key))
        r.raise_for_status()
        rows = r.json()
    _print_table(rows, ["id", "event_type", "job_id", "message"])


@rooms_app.command("list")
def rooms_list(
    project_id: int = typer.Option(DEFAULT_PROJECT, "--project-id"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
) -> None:
    with _client() as client:
        r = client.get(f"/projects/{project_id}/rooms", headers=_headers(api_key))
        r.raise_for_status()
        rows = r.json()
    _print_table(rows, ["id", "slug", "name"])


@rooms_app.command("read")
def rooms_read(
    slug: str,
    project_id: int = typer.Option(DEFAULT_PROJECT, "--project-id"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
) -> None:
    with _client() as client:
        r = client.get(f"/projects/{project_id}/rooms/{slug}", headers=_headers(api_key))
        r.raise_for_status()
        rows = r.json()
    if not rows:
        typer.echo("(empty)")
        return
    for m in rows:
        typer.echo(f"[{m['id']}] agent={m['agent_type']} job={m['job_id']}")
        typer.echo(m["body"])
        typer.echo("---")


@review_app.command("approve")
def review_approve(
    job_id: int,
    project_id: int = typer.Option(DEFAULT_PROJECT, "--project-id"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
    wait: bool = typer.Option(False, "--wait"),
) -> None:
    with _client() as client:
        r = client.post(
            f"/projects/{project_id}/reviews/{job_id}/approve",
            headers=_headers(api_key),
        )
        if r.status_code >= 400:
            typer.echo(r.text)
            raise typer.Exit(1)
        data = r.json()
    typer.echo(f"status={data['status']} checklist_job_id={data['checklist_job_id']}")
    if wait:
        n = drain_queue()
        typer.echo(f"worker_processed={n}")


@app.command()
def worker(
    once: bool = typer.Option(False, "--once"),
    poll: float = typer.Option(1.0, "--poll"),
) -> None:
    run_worker(poll_seconds=poll, once=once)


@app.command()
def drain() -> None:
    n = drain_queue()
    typer.echo(f"processed={n}")


@app.command()
def speak(
    text: str = typer.Argument(...),
    out: Optional[str] = typer.Option(None, "--out", help="Optional output wav path"),
) -> None:
    """Groq TTS proof (debug)."""
    from app.services.tts import TTSError, synthesize_speech
    from pathlib import Path
    import shutil

    try:
        path = synthesize_speech(text)
    except TTSError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1)
    if out:
        dest = Path(out)
        shutil.copy(path, dest)
        typer.echo(f"wrote {dest}")
    else:
        typer.echo(f"wrote {path}")


@app.command()
def status(
    project_id: int = typer.Option(DEFAULT_PROJECT, "--project-id"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
) -> None:
    with _client() as client:
        jobs = client.get(f"/projects/{project_id}/jobs", headers=_headers(api_key)).json()
        audit_rows = client.get(f"/projects/{project_id}/audit", headers=_headers(api_key)).json()
        rooms = client.get(f"/projects/{project_id}/rooms", headers=_headers(api_key)).json()
        counts = {}
        for room in rooms:
            msgs = client.get(
                f"/projects/{project_id}/rooms/{room['slug']}",
                headers=_headers(api_key),
            ).json()
            counts[room["slug"]] = len(msgs)
    active = [j for j in jobs if j["status"] in ("queued", "running")]
    typer.echo(f"active_jobs={len(active)} total_jobs={len(jobs)}")
    typer.echo("room_message_counts=" + json.dumps(counts))
    typer.echo("--- last audit ---")
    _print_table(audit_rows[-10:], ["id", "event_type", "message"])


@app.command()
def demo(
    project_id: int = typer.Option(DEFAULT_PROJECT, "--project-id"),
) -> None:
    """Demo: objectives + progress bar + auto-router (no @mentions)."""
    typer.echo("=== AIO DEMO ===")
    typer.echo("Buzz: @mention agents in chat")
    typer.echo("AIO: set objectives -> run -> progress bar fills -> checklist")
    typer.echo("")
    headers = _headers()
    with _client() as client:
        # reset-ish: add fresh demo objectives if few exist
        existing = client.get(
            f"/projects/{project_id}/objectives", headers=headers
        ).json()
        if existing["total"] == 0:
            for title in (
                "Research Dubai metro tips",
                "Draft a short brief about metro tips",
                "Create follow-up tasks from the brief",
            ):
                client.post(
                    f"/projects/{project_id}/objectives",
                    headers=headers,
                    json={"title": title},
                ).raise_for_status()

        prog = client.get(f"/projects/{project_id}/objectives", headers=headers).json()
        typer.echo("starting objectives:")
        _print_objectives(prog)
        typer.echo("")

        pending = [o for o in prog["objectives"] if not o["done"]]
        if not pending:
            typer.echo("all objectives already done - add more with ./aio objectives add")
        else:
            first = pending[0]
            typer.echo(f"> run objective {first['id']}: {first['title']}")
            r = client.post(
                f"/projects/{project_id}/objectives/{first['id']}/run",
                headers=headers,
            )
            r.raise_for_status()
            data = r.json()
            typer.echo(f"routed agents={data['agents']} jobs={data['job_ids']}")
            typer.echo("working...")
            n = drain_queue(max_jobs=30)
            typer.echo(f"worker_processed={n}")
            done = client.post(
                f"/projects/{project_id}/objectives/{first['id']}/complete",
                headers=headers,
            )
            if done.status_code >= 400:
                typer.echo(f"complete skipped: {done.text}")
            prog = client.get(
                f"/projects/{project_id}/objectives", headers=headers
            ).json()
            typer.echo("")
            typer.echo("after run:")
            _print_objectives(prog)

        typer.echo("")
        items = client.get(f"/projects/{project_id}/checklist", headers=headers).json()
        _print_checklist(items)
        typer.echo("")
        general = client.get(
            f"/projects/{project_id}/rooms/general", headers=headers
        ).json()
        typer.echo(f"general room messages: {len(general)} (should stay quiet)")
    typer.echo("=== END DEMO ===")


@app.command("webhook-sim")
def webhook_sim(
    repo: str = "example/demo-project",
    diff: str = "diff --git a/x.py b/x.py\n+print('hello')\n",
    action: str = "opened",
    title: str = "demo PR #obj-1",
    pr_number: int = 42,
    merged: bool = False,
) -> None:
    """Simulate a signed GitHub webhook locally (opened | synchronize | closed)."""
    settings = get_settings()
    pr = {
        "title": title,
        "body": f"Links to {title}",
        "diff_text": diff,
        "html_url": f"https://github.com/{repo}/pull/{pr_number}",
        "number": pr_number,
        "merged": merged,
        "head": {"ref": f"aio/obj-branch"},
    }
    if action == "closed" and merged:
        action = "closed"
        pr["merged"] = True
    payload = {
        "action": action,
        "repository": {"full_name": repo},
        "pull_request": pr,
        "diff_text": diff,
    }
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(
        settings.github_webhook_secret.encode(), body, hashlib.sha256
    ).hexdigest()
    with _client() as client:
        r = client.post(
            "/webhooks/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": sig,
                "X-GitHub-Delivery": f"sim-{int(time.time())}-{action}",
                "X-GitHub-Event": "pull_request",
            },
        )
        typer.echo(r.text)
        if r.status_code >= 400:
            raise typer.Exit(1)


def main() -> None:
    try:
        app()
    except httpx.ConnectError:
        from app.cli_pkg.session import resolve_base_url

        typer.echo(
            f"cannot reach the AIO API at {resolve_base_url()}\n"
            "start it with: uvicorn app.main:app --host 0.0.0.0 --port 8000"
        )
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
