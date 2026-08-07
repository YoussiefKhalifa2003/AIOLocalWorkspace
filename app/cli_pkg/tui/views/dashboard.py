"""Dashboard tab: owner-only analytics — people, models, open work."""

from __future__ import annotations

from typing import Any

from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import DataTable, Label, Static

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
        self._signature = ""

    def compose(self) -> ComposeResult:
        yield Label("DASHBOARD", classes="view-head")
        yield self.note
        yield self.stats
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

        self.tasks.clear()
        for t in data.get("open_tasks", []):
            self.tasks.add_row(
                str(t.get("id")),
                str(t.get("title") or ""),
                str(t.get("status") or ""),
                str(t.get("assignee_email") or "-"),
            )

    def show_owner_only(self) -> None:
        self.note.update("[yellow]Dashboard is owner-only.[/yellow]")
