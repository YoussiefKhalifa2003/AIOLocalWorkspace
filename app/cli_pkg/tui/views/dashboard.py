"""Dashboard tab: owner-only analytics - people, models, open work."""

from __future__ import annotations

from typing import Any

from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, DataTable, Label, Select, Static

from app.cli_pkg.tui.client import ApiClient, ApiError


class StatCard(Static):
    def __init__(self, label: str, value: Any, accent: bool = False) -> None:
        colour = "#7dd3fc" if accent else "white"
        super().__init__(f"[b {colour}]{value}[/]\n[dim]{label}[/dim]", markup=True, classes="stat")


class DashboardView(VerticalScroll):
    def __init__(self, client: ApiClient) -> None:
        super().__init__(id="dashboard")
        self.client = client
        self.stats = Horizontal(id="stat-row")
        self.people = DataTable(id="people-table", zebra_stripes=True, cursor_type="row")
        self.models = DataTable(id="models-table", zebra_stripes=True, cursor_type="row")
        self.tasks = DataTable(id="tasks-table", zebra_stripes=True, cursor_type="row")
        self.note = Static("", id="dash-note", markup=True)
        self.obj_select: Select[str] = Select[str]((), id="dash-obj", prompt="pick a task…")
        self.member_select: Select[str] = Select[str]((), id="dash-member", prompt="pick a person…")
        self.assign_btn = Button("Assign", id="dash-assign-btn", variant="primary")
        self._signature = ""
        self._open_tasks: list[dict[str, Any]] = []
        self._members: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        yield Label("DASHBOARD", classes="view-head")
        yield self.note
        yield self.stats
        with Horizontal(id="dash-assign"):
            yield Label("Assign", id="dash-assign-label")
            yield self.obj_select
            yield self.member_select
            yield self.assign_btn
        yield Label("people", classes="table-head")
        yield self.people
        yield Label("models", classes="table-head")
        yield self.models
        yield Label("open work", classes="table-head")
        yield self.tasks

    def on_mount(self) -> None:
        self.people.add_columns("person", "role", "jobs", "tokens", "models")
        self.models.add_columns("model", "backend", "runs", "tokens", "ok", "fail")
        self.tasks.add_columns("#", "task", "status", "assignee")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "dash-assign-btn":
            event.stop()
            self._do_assign()

    def _do_assign(self) -> None:
        oid_raw = self.obj_select.value
        uid_raw = self.member_select.value
        if oid_raw is Select.NULL or uid_raw is Select.NULL or not oid_raw or not uid_raw:
            self.app.set_status("[yellow]pick a task and a person[/yellow]")
            return
        try:
            oid = int(oid_raw)
            uid = int(uid_raw)
        except (TypeError, ValueError):
            self.app.set_status("[yellow]pick a task and a person[/yellow]")
            return
        task_title = next(
            (str(t.get("title") or f"#{oid}") for t in self._open_tasks if int(t.get("id") or 0) == oid),
            f"#{oid}",
        )
        who = next(
            (
                str(m.get("name") or m.get("email") or uid)
                for m in self._members
                if int(m.get("user_id") or m.get("id") or 0) == uid
            ),
            str(uid),
        )
        self.app.set_status(f"[dim]assigning {escape(task_title)} → {escape(who)}…[/dim]")
        self._assign_worker(oid, uid, task_title, who)

    @work(thread=True, group="dashboard-assign")
    def _assign_worker(
        self, objective_id: int, assignee_user_id: int, task_title: str, who: str
    ) -> None:
        try:
            self.client.assign(objective_id, assignee_user_id)
            msg = f"[green]assigned {escape(task_title)} → {escape(who)}[/green]"
        except ApiError as exc:
            msg = f"[red]assign failed: {escape(str(exc))}[/red]"
        self.app.call_from_thread(self.app.set_status, msg)
        self.app.call_from_thread(self.load)

    @work(thread=True, exclusive=True, group="dashboard")
    def load(self) -> None:
        try:
            data = self.client.analytics()
            error = ""
        except ApiError as exc:
            data, error = {}, str(exc)
        self.app.call_from_thread(self._apply, data, error)

    def _apply(self, data: dict[str, Any], error: str) -> None:
        if error:
            self.note.update(f"[red]{escape(error)}[/red]")
            return
        signature = str(data)
        if signature == self._signature:
            return
        self._signature = signature
        self.note.update("")

        summary = data.get("summary") or {}
        self.stats.remove_children()
        self.stats.mount_all(
            [
                StatCard("people", summary.get("members", 0)),
                StatCard("open tasks", summary.get("open_tasks", 0)),
                StatCard("jobs done", summary.get("jobs_done", 0)),
                StatCard("failed", summary.get("jobs_failed", 0)),
                StatCard("tokens", summary.get("tokens_total", 0), accent=True),
                StatCard("models", summary.get("model_count", 0)),
            ]
        )

        self.people.clear()
        for p in data.get("people", []):
            names = p.get("models") or []
            self.people.add_row(
                str(p.get("name") or p.get("email") or "-"),
                str(p.get("role") or ""),
                str(p.get("jobs", 0)),
                str(p.get("tokens", 0)),
                ", ".join(names[:2]) + (f" +{len(names) - 2}" if len(names) > 2 else ""),
            )

        self.models.clear()
        for m in data.get("models", []):
            self.models.add_row(
                str(m.get("model") or "-"),
                str(m.get("backend") or ""),
                str(m.get("runs", 0)),
                str(m.get("tokens", 0)),
                str(m.get("success", 0)),
                str(m.get("fail", 0)),
            )

        self._open_tasks = list(data.get("open_tasks") or [])
        self.tasks.clear()
        for t in self._open_tasks:
            self.tasks.add_row(
                str(t.get("id")),
                str(t.get("title") or ""),
                str(t.get("status") or ""),
                str(t.get("assignee_email") or "-"),
            )

        # Textual Select options are (display label, internal value) - label first.
        obj_opts: list[tuple[str, str]] = []
        for t in self._open_tasks:
            if t.get("id") is None:
                continue
            title = str(t.get("title") or "").strip() or f"task #{t['id']}"
            status = str(t.get("status") or "").replace("_", " ")
            label = title if not status else f"{title}  |  {status}"
            if len(label) > 56:
                label = label[:55] + "…"
            obj_opts.append((label, str(t["id"])))
        self.obj_select.set_options(obj_opts)

        members: list[dict[str, Any]] = []
        ws = getattr(self.app, "ws", None)
        if ws is not None:
            members = list(ws.members or [])
        if not members:
            members = list(data.get("people") or [])
        self._members = members
        mem_opts: list[tuple[str, str]] = []
        for m in members:
            uid = m.get("user_id") or m.get("id")
            if not uid:
                continue
            name = str(m.get("name") or "").strip()
            email = str(m.get("email") or "").strip()
            # Prefer display name; fall back to email local-part, never bare id.
            if name and email and name.lower() != email.lower():
                label = f"{name}  |  {email}"
            else:
                label = name or email or f"user {uid}"
            if len(label) > 48:
                label = label[:47] + "…"
            mem_opts.append((label, str(uid)))
        self.member_select.set_options(mem_opts)

    def show_owner_only(self) -> None:
        self.note.update("[yellow]Dashboard is owner-only.[/yellow]")
