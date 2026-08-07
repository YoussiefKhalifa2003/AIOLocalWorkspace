"""Board tab: columns, card detail, agent dispatch, confirm-merge."""

from __future__ import annotations

import webbrowser
from typing import Any

from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, HorizontalScroll
from textual.widgets import Input

from app.cli_pkg.tui.client import ApiClient, ApiError, board_fingerprint
from app.cli_pkg.tui.widgets import BoardColumn, ChoiceModal, ConfirmModal, DetailPane, PromptModal
from app.config import get_settings
from app.services.board import BOARD_COLUMNS

MERGE_WARNING = "Merging into the default branch cannot be easily undone."


class BoardView(Horizontal):
    def __init__(self, client: ApiClient) -> None:
        super().__init__(id="board")
        self.client = client
        self.columns: dict[str, BoardColumn] = {}
        self.detail = DetailPane()
        self.board: dict[str, Any] = {}
        self._fingerprint = ""
        self._order = list(BOARD_COLUMNS)
        self._index = 0

    def compose(self) -> ComposeResult:
        # Seven columns never fit a terminal at a readable width, so the strip
        # scrolls sideways and follows the focused column.
        with HorizontalScroll(id="columns"):
            for status in self._order:
                col = BoardColumn(status)
                self.columns[status] = col
                yield col
        yield self.detail

    # data ----------------------------------------------------------------

    def apply(self, board: dict[str, Any], jobs_today: int) -> None:
        self.board = board
        fp = board_fingerprint(board, jobs_today)
        if fp == self._fingerprint:
            return
        self._fingerprint = fp
        for col in board.get("columns", []):
            widget = self.columns.get(col["id"])
            if widget is None:
                continue
            widget.set_cards(
                [{**c, "status": c.get("status") or col["id"]} for c in col.get("cards", [])]
            )
        self.detail.show(self.current_card)

    def invalidate(self) -> None:
        self._fingerprint = ""

    @property
    def agent_working(self) -> int:
        for col in self.board.get("columns", []):
            if col.get("id") == "agent_backlog":
                return len(col.get("cards", []))
        return 0

    # selection -----------------------------------------------------------

    @property
    def current_column(self) -> BoardColumn:
        return self.columns[self._order[self._index]]

    @property
    def current_card(self) -> dict[str, Any] | None:
        return self.current_column.selected

    def focus_column(self, index: int) -> None:
        self._index = index % len(self._order)
        column = self.current_column
        column.list_view.focus()
        column.scroll_visible(animate=False)
        self.detail.show(self.current_card)

    def focus_selected(self) -> None:
        self.focus_column(self._index)

    def move_column(self, delta: int) -> None:
        self.focus_column(self._index + delta)

    def move_card(self, delta: int) -> None:
        lv = self.current_column.list_view
        lv.action_cursor_down() if delta > 0 else lv.action_cursor_up()
        self.detail.show(self.current_card)

    def refresh_detail(self) -> None:
        self.detail.show(self.current_card)

    # actions -------------------------------------------------------------

    def add_card(self) -> None:
        def done(title: str | None) -> None:
            if title:
                self._add_worker(title)

        self.app.push_screen(PromptModal("New objective", "title"), done)

    @work(thread=True, group="board")
    def _add_worker(self, title: str) -> None:
        try:
            obj = self.client.add_objective(title)
            msg = f"added #{obj.get('id')} {title}"
        except ApiError as exc:
            msg = f"[red]{escape(str(exc))}[/red]"
        self.app.call_from_thread(self.app.after_mutation, msg)

    def change_status(self) -> None:
        card = self.current_card
        if not card:
            return

        def done(choice: str | None) -> None:
            if choice:
                self.apply_status(card, choice)

        self.app.push_screen(ChoiceModal(f"Move #{card['id']} to…", list(BOARD_COLUMNS)), done)

    def send_to_agent(self) -> None:
        card = self.current_card
        if not card:
            return
        from app.cli_pkg.doctor import available_coding_runners

        runners = available_coding_runners()
        if len(runners) <= 1:
            self.apply_status(card, "agent_backlog")
            return

        def done(choice: str | None) -> None:
            if choice:
                self.apply_status(card, "agent_backlog", runner=choice)

        self.app.push_screen(ChoiceModal(f"Runner for #{card['id']}", runners), done)

    def apply_status(self, card: dict[str, Any], status: str, runner: str = "") -> None:
        self.app.set_status(f"#{card['id']} -> {status}…")
        self._status_worker(int(card["id"]), status, runner)

    @work(thread=True, group="board")
    def _status_worker(self, objective_id: int, status: str, runner: str) -> None:
        try:
            self.client.set_status(objective_id, status, runner=runner)
            note = f" via {runner}" if runner else ""
            msg = f"#{objective_id} -> {status}{note}"
        except ApiError as exc:
            msg = f"[red]{escape(str(exc))}[/red]"
        self.app.call_from_thread(self.app.after_mutation, msg)

    def merge_card(self) -> None:
        card = self.current_card
        if not card:
            return
        if not card.get("can_merge"):
            self.app.set_status(
                f"[red]#{card['id']} is not mergeable "
                f"(status={card.get('status')}, pr={card.get('pr_url') or 'none'})[/red]"
            )
            return
        detail = (
            f"#{card['id']} {escape(str(card.get('title') or ''))}\n"
            f"PR #{card.get('pr_number')} · {card.get('pr_url')}\n"
            f"branch {card.get('github_branch') or '-'} · method {get_settings().merge_method}"
        )

        def done(confirmed: bool | None) -> None:
            if not confirmed:
                self.app.set_status("merge cancelled")
                return
            self.app.set_status(f"merging PR #{card.get('pr_number')}…")
            self._merge_worker(int(card["id"]))

        self.app.push_screen(
            ConfirmModal("Merge & done", detail, MERGE_WARNING, "Merge & move to done"), done
        )

    @work(thread=True, group="board")
    def _merge_worker(self, objective_id: int) -> None:
        try:
            data = self.client.merge(objective_id)
            sha = str(data.get("sha") or "")[:8]
            msg = f"#{objective_id} merged into {data.get('base')} ({sha}) · card is done"
        except ApiError as exc:
            msg = f"[red]{escape(str(exc))}[/red]"
        self.app.call_from_thread(self.app.after_mutation, msg)

    def open_pr(self) -> None:
        url = (self.current_card or {}).get("pr_url")
        if not url:
            self.app.set_status("no PR on this card")
            return
        webbrowser.open(url)
        self.app.set_status(f"opened {url}")

    def copy_pr(self) -> None:
        url = (self.current_card or {}).get("pr_url")
        if not url:
            self.app.set_status("no PR on this card")
            return
        try:
            self.app.copy_to_clipboard(url)
            self.app.set_status(f"copied {url}")
        except Exception:  # noqa: BLE001 - clipboard is best effort over SSH
            self.app.set_status(url)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
