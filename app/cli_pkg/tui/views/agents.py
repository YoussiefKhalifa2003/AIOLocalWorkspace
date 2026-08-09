"""Agents tab: pick the model behind each /skill."""

from __future__ import annotations

from typing import Any

from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Label, Select, Static

from app.cli_pkg.tui.client import ApiClient, ApiError

SKILL_FOR_AGENT = {
    "ask": "/ask",
    "deepresearch": "/deepresearch",
    "writing": "/write",
    "coding": "/code",
    "code_review": "/review",
    "checklist": "/checklist",
    "status": "/status",
}


class AgentsView(VerticalScroll):
    def __init__(self, client: ApiClient) -> None:
        super().__init__(id="agents")
        self.client = client
        self.data: dict[str, Any] = {}
        self.selects: dict[str, Select] = {}
        self.rows = VerticalScroll(id="agent-rows")
        self.info = Static("", id="agent-info", markup=True)
        self._loaded = False

    def compose(self) -> ComposeResult:
        yield Label("AGENTS", classes="view-head")
        yield Static(
            "Each /skill is backed by a model. Changes apply workspace-wide.",
            classes="view-sub",
        )
        yield self.info
        yield self.rows
        with Horizontal(id="agent-actions"):
            yield Button("Save", variant="primary", id="agents-save")
            yield Button("Reload", id="agents-reload")

    def on_mount(self) -> None:
        self.load()

    @work(thread=True, group="agents")
    def load(self) -> None:
        try:
            data = self.client.agent_models()
            error = ""
        except ApiError as exc:
            data, error = {}, str(exc)
        self.app.call_from_thread(self._apply, data, error)

    def _apply(self, data: dict[str, Any], error: str) -> None:
        if error:
            self.info.update(f"[red]{escape(error)}[/red]")
            return
        self.data = data
        providers = [
            f"{name} {'[green]on[/green]' if data.get(f'{name}_configured') else '[dim]off[/dim]'}"
            for name in ("gemini", "openrouter", "opencode", "github")
        ]
        self.info.update(
            " | ".join(providers) + f"  |  [dim]backend {data.get('backend', '?')}[/dim]"
        )

        options = [(str(m.get("label") or m.get("id")), str(m["id"])) for m in data.get("models", [])]
        prefs = data.get("prefs") or {}
        self.rows.remove_children()
        self.selects.clear()
        for agent in data.get("agents", []):
            current = str(prefs.get(agent) or "")
            values = {v for _, v in options}
            row_options = list(options)
            if current and current not in values:
                row_options.insert(0, (f"{current} (custom)", current))
            select: Select = Select(
                row_options, value=current or Select.BLANK, allow_blank=True, id=f"sel-{agent}"
            )
            self.selects[agent] = select
            skill = SKILL_FOR_AGENT.get(agent, "")
            label = f"[b]{escape(agent)}[/b]" + (f"  [dim]{skill}[/dim]" if skill else "")
            self.rows.mount(
                Horizontal(
                    Static(label, markup=True, classes="agent-name"),
                    select,
                    classes="agent-row",
                )
            )
        self._loaded = True

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "agents-save":
            self.save()
        elif event.button.id == "agents-reload":
            self.info.update("[dim]reloading…[/dim]")
            self.load()

    def save(self) -> None:
        if not self._loaded:
            return
        prefs = {
            agent: str(sel.value)
            for agent, sel in self.selects.items()
            if sel.value is not Select.BLANK and sel.value
        }
        if not prefs:
            self.app.set_status("nothing to save")
            return
        self.app.set_status("saving agent models…")
        self._save_worker(prefs)

    @work(thread=True, group="agents")
    def _save_worker(self, prefs: dict[str, str]) -> None:
        try:
            self.client.save_agent_models(prefs)
            msg = f"saved {len(prefs)} agent model(s)"
        except ApiError as exc:
            msg = f"[red]{escape(str(exc))}[/red]"
        self.app.call_from_thread(self.app.set_status, msg)
