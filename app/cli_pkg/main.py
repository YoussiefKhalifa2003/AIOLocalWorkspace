from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any, Optional

import httpx
import typer

from app import __version__
from app.config import get_settings
from app.db.session import init_db
from app.services.seed import seed_demo_data
from app.worker import drain_queue, run_worker

app = typer.Typer(add_completion=False, no_args_is_help=True, help="AIO metal CLI")
jobs_app = typer.Typer(help="Jobs")
rooms_app = typer.Typer(help="Rooms")
review_app = typer.Typer(help="Reviews")
objectives_app = typer.Typer(help="Objectives + progress")
checklist_app = typer.Typer(help="Checklist")
app.add_typer(jobs_app, name="jobs")
app.add_typer(rooms_app, name="rooms")
app.add_typer(review_app, name="review")
app.add_typer(objectives_app, name="objectives")
app.add_typer(checklist_app, name="checklist")


def _headers(api_key: str | None = None, email: str = "a@local.test") -> dict[str, str]:
    settings = get_settings()
    key = api_key or settings.demo_api_key
    h = {"X-API-Key": key}
    join = settings.workspace_join_key or settings.demo_api_key
    if key == join:
        h["X-User-Email"] = email
    return h


def _client() -> httpx.Client:
    settings = get_settings()
    return httpx.Client(base_url=settings.api_base_url, timeout=60.0)


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
def seed() -> None:
    init_db()
    info = seed_demo_data()
    typer.echo("seeded:")
    for k, v in info.items():
        typer.echo(f"  {k}={v}")


@app.command("projects")
def projects_list(
    api_key: Optional[str] = typer.Option(None, "--api-key"),
) -> None:
    with _client() as client:
        r = client.get("/projects", headers=_headers(api_key))
        r.raise_for_status()
        rows = r.json()
    _print_table(rows, ["id", "tenant_id", "name", "github_repo"])


@app.command()
def ask(
    text: str = typer.Argument(...),
    project_id: int = typer.Option(1, "--project-id"),
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
    project_id: int = typer.Option(1, "--project-id"),
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
    project_id: int = typer.Option(1, "--project-id"),
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
    project_id: int = typer.Option(1, "--project-id"),
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
    project_id: int = typer.Option(1, "--project-id"),
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
    project_id: int = typer.Option(1, "--project-id"),
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
    project_id: int = typer.Option(1, "--project-id"),
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
    project_id: int = typer.Option(1, "--project-id"),
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
    project_id: int = typer.Option(1, "--project-id"),
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
    project_id: int = typer.Option(1, "--project-id"),
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
    project_id: int = typer.Option(1, "--project-id"),
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
    project_id: int = typer.Option(1, "--project-id"),
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
    project_id: int = typer.Option(1, "--project-id"),
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


@app.command()
def run(
    objective_id: int = typer.Argument(...),
    project_id: int = typer.Option(1, "--project-id"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
    wait: bool = typer.Option(True, "--wait/--no-wait"),
) -> None:
    """Shortcut: ./aio run <objective_id>"""
    objectives_run(
        objective_id=objective_id,
        project_id=project_id,
        api_key=api_key,
        wait=wait,
    )


@checklist_app.callback(invoke_without_command=True)
def checklist_root(
    ctx: typer.Context,
    project_id: int = typer.Option(1, "--project-id"),
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
    project_id: int = typer.Option(1, "--project-id"),
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
    project_id: int = typer.Option(1, "--project-id"),
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
    project_id: int = typer.Option(1, "--project-id"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
) -> None:
    with _client() as client:
        r = client.get(f"/projects/{project_id}/audit", headers=_headers(api_key))
        r.raise_for_status()
        rows = r.json()
    _print_table(rows, ["id", "event_type", "job_id", "message"])


@rooms_app.command("list")
def rooms_list(
    project_id: int = typer.Option(1, "--project-id"),
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
    project_id: int = typer.Option(1, "--project-id"),
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
    project_id: int = typer.Option(1, "--project-id"),
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
    project_id: int = typer.Option(1, "--project-id"),
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
    project_id: int = typer.Option(1, "--project-id"),
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
    app()


if __name__ == "__main__":
    main()
