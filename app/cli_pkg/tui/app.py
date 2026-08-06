"""Owner-only live board dashboard."""

from __future__ import annotations

import webbrowser
from typing import Any

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Footer, Header, Static

from app.cli_pkg.tui.data import BoardClient, OwnerRequired, Snapshot
from app.cli_pkg.tui.widgets import BoardColumn, ChoiceModal, ConfirmModal, DetailPane
from app.config import get_settings
from app.services.board import BOARD_COLUMNS

MERGE_WARNING = "Merging into the default branch cannot be easily undone."


class AioTui(App[None]):
    CSS = """
    Screen { layout: vertical; }
    #columns { height: 1fr; }
    BoardColumn {
        width: 1fr;
        border: round $panel-lighten-2;
        padding: 0 1;
    }
    BoardColumn:focus-within { border: round $accent; }
    #detail { width: 42; border: round $panel-lighten-2; padding: 0 1; }
    #status-line { height: 1; color: $text-muted; padding: 0 1; }
    #confirm-box {
        width: 62;
        height: auto;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }
    #confirm-title { text-style: bold; padding-bottom: 1; }
    ListItem { padding: 0 1; }
    """

    BINDINGS = [
        ("q", "quit", "quit"),
        ("r", "refresh", "refresh"),
        ("j", "next_card", "down"),
        ("k", "prev_card", "up"),
        ("h", "prev_column", "left"),
        ("l", "next_column", "right"),
        ("enter", "show_detail", "detail"),
        ("s", "change_status", "status"),
        ("a", "send_to_agent", "agent"),
        ("m", "merge_card", "merge"),
        ("o", "open_pr", "open PR"),
        ("y", "copy_pr", "copy PR"),
    ]

    def __init__(self, client: BoardClient, *, poll_seconds: float = 2.0, me: dict | None = None):
        super().__init__()
        self.client = client
        self.poll_seconds = max(0.5, float(poll_seconds))
        self.me = me or {}
        self.columns: dict[str, BoardColumn] = {}
        self.detail = DetailPane()
        self.status_line = Static("", id="status-line", markup=True)
        self.snapshot = Snapshot()
        self._fingerprint = ""
        self._message = ""
        self._col_order = list(BOARD_COLUMNS)
        self._col_index = 0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="columns"):
            for status in self._col_order:
                col = BoardColumn(status)
                self.columns[status] = col
                yield col
            yield self.detail
        yield self.status_line
        yield Footer()

    def on_mount(self) -> None:
        self.title = "AIO"
        self.sub_title = f"project {self.client.project_id} · {self.me.get('email', '')}"
        self.refresh_board()
        self.set_interval(self.poll_seconds, self.refresh_board)
        self._focus_column(0)

    # data ---------------------------------------------------------------

    @work(thread=True, exclusive=True, group="poll")
    def refresh_board(self) -> None:
        """Fetch off the UI thread so a slow API never freezes the dashboard."""
        snap = self.client.snapshot()
        self.call_from_thread(self._apply_snapshot, snap)

    def _apply_snapshot(self, snap: Snapshot) -> None:
        if snap.error:
            self._set_status(f"[red]{snap.error}[/red]")
            return
        self.snapshot = snap
        if snap.fingerprint != self._fingerprint:
            self._fingerprint = snap.fingerprint
            self._render_board()
        self._set_status()

    def _render_board(self) -> None:
        for col in self.snapshot.board.get("columns", []):
            widget = self.columns.get(col["id"])
            if widget is None:
                continue
            cards = [{**c, "status": c.get("status") or col["id"]} for c in col.get("cards", [])]
            widget.set_cards(cards)
        self.detail.show(self.current_card)

    def _set_status(self, message: str | None = None) -> None:
        if message is not None:
            self._message = message
        backlog = len(
            next(
                (
                    c.get("cards", [])
                    for c in self.snapshot.board.get("columns", [])
                    if c["id"] == "agent_backlog"
                ),
                [],
            )
        )
        runner = get_settings().coding_backend or "llm"
        repo = self.snapshot.board.get("github_repo") or "no repo"
        parts = [
            f"repo {repo}",
            f"jobs {self.snapshot.jobs_today}",
            f"agent working {backlog}",
            f"runner {runner}",
            f"mentions {self.snapshot.unread_mentions}",
        ]
        line = " · ".join(parts)
        if self._message:
            line = f"{line}   {self._message}"
        self.status_line.update(line)

    # selection ----------------------------------------------------------

    @property
    def current_column(self) -> BoardColumn:
        return self.columns[self._col_order[self._col_index]]

    @property
    def current_card(self) -> dict[str, Any] | None:
        return self.current_column.selected

    def _focus_column(self, index: int) -> None:
        self._col_index = index % len(self._col_order)
        self.current_column.list_view.focus()
        self.detail.show(self.current_card)

    def action_next_column(self) -> None:
        self._focus_column(self._col_index + 1)

    def action_prev_column(self) -> None:
        self._focus_column(self._col_index - 1)

    def action_next_card(self) -> None:
        lv = self.current_column.list_view
        lv.action_cursor_down()
        self.detail.show(self.current_card)

    def action_prev_card(self) -> None:
        lv = self.current_column.list_view
        lv.action_cursor_up()
        self.detail.show(self.current_card)

    def action_show_detail(self) -> None:
        self.detail.show(self.current_card)

    def action_refresh(self) -> None:
        self._fingerprint = ""
        self._set_status("refreshing…")
        self.refresh_board()

    # actions ------------------------------------------------------------

    def action_change_status(self) -> None:
        card = self.current_card
        if not card:
            return

        def done(choice: str | None) -> None:
            if not choice:
                return
            self._apply_status(card, choice)

        self.push_screen(ChoiceModal(f"Move #{card['id']} to…", list(BOARD_COLUMNS)), done)

    def action_send_to_agent(self) -> None:
        card = self.current_card
        if not card:
            return
        from app.cli_pkg.doctor import available_coding_runners

        runners = available_coding_runners()
        if len(runners) <= 1:
            self._apply_status(card, "agent_backlog")
            return

        def done(choice: str | None) -> None:
            if not choice:
                return
            self._apply_status(card, "agent_backlog", runner=choice)

        self.push_screen(ChoiceModal(f"Runner for #{card['id']}", runners), done)

    def _apply_status(self, card: dict[str, Any], status: str, runner: str = "") -> None:
        self._set_status(f"#{card['id']} -> {status}…")
        self._status_worker(int(card["id"]), status, runner)

    @work(thread=True, group="mutate")
    def _status_worker(self, objective_id: int, status: str, runner: str) -> None:
        err = self.client.set_status(objective_id, status, runner=runner)
        note = f" via {runner}" if runner else ""
        message = f"[red]{err}[/red]" if err else f"#{objective_id} -> {status}{note}"
        self.call_from_thread(self._after_mutation, message)

    def _after_mutation(self, message: str) -> None:
        self._fingerprint = ""
        self._set_status(message)
        self.refresh_board()

    def action_merge_card(self) -> None:
        card = self.current_card
        if not card:
            return
        if not card.get("can_merge"):
            self._set_status(
                f"[red]#{card['id']} is not mergeable "
                f"(status={card.get('status')}, pr={card.get('pr_url') or 'none'})[/red]"
            )
            return

        detail = (
            f"#{card['id']} {card.get('title')}\n"
            f"PR #{card.get('pr_number')} · {card.get('pr_url')}\n"
            f"branch {card.get('github_branch') or '-'} · "
            f"method {get_settings().merge_method}"
        )

        def done(confirmed: bool | None) -> None:
            if not confirmed:
                self._set_status("merge cancelled")
                return
            self._set_status(f"merging PR #{card.get('pr_number')}…")
            self._merge_worker(int(card["id"]))

        self.push_screen(
            ConfirmModal("Merge & done", detail, MERGE_WARNING, "Merge & move to done"),
            done,
        )

    @work(thread=True, group="mutate")
    def _merge_worker(self, objective_id: int) -> None:
        ok, msg = self.client.merge(objective_id)
        message = (
            f"#{objective_id} {msg} · card is done" if ok else f"[red]{msg}[/red]"
        )
        self.call_from_thread(self._after_mutation, message)

    def action_open_pr(self) -> None:
        card = self.current_card
        url = (card or {}).get("pr_url")
        if not url:
            self._set_status("no PR on this card")
            return
        webbrowser.open(url)
        self._set_status(f"opened {url}")

    def action_copy_pr(self) -> None:
        card = self.current_card
        url = (card or {}).get("pr_url")
        if not url:
            self._set_status("no PR on this card")
            return
        try:
            self.copy_to_clipboard(url)
            self._set_status(f"copied {url}")
        except Exception:  # noqa: BLE001 - clipboard is best effort over SSH
            self._set_status(url)


def run_tui(project_id: int, *, poll_seconds: float = 2.0, api_key: str = "", email: str = "") -> int:
    """Owner gate, then launch. Returns a process exit code."""
    client = BoardClient(project_id, api_key=api_key or None, email=email or None)
    try:
        me = client.require_owner()
    except OwnerRequired as exc:
        print(str(exc))
        return 2
    except Exception as exc:  # noqa: BLE001 - surface connection issues plainly
        print(f"cannot reach the AIO API: {exc}")
        return 2
    AioTui(client, poll_seconds=poll_seconds, me=me).run()
    return 0
