"""Live tab: real-time gauges, sparklines, and bars (owner-only)."""

from __future__ import annotations

from typing import Any

from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Label, ProgressBar, Sparkline, Static

from app.cli_pkg.tui.client import (
    ApiClient,
    ApiError,
    RingBuffer,
    column_counts,
    live_fingerprint,
)
from app.services.board import BOARD_COLUMNS

BAR_WIDTH = 18


def _bar(value: float, maximum: float, width: int = BAR_WIDTH) -> str:
    if maximum <= 0:
        filled = 0
    else:
        filled = int(round(max(0.0, min(1.0, value / maximum)) * width))
    return "█" * filled + "░" * (width - filled)


def _short(name: str, n: int = 18) -> str:
    text = (name or "-").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def _fmt_int(n: Any) -> str:
    try:
        v = int(n or 0)
    except (TypeError, ValueError):
        return "0"
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 10_000:
        return f"{v / 1_000:.1f}k"
    return str(v)


class GaugeCard(Vertical):
    """One labeled ProgressBar gauge."""

    def __init__(self, title: str, gauge_id: str) -> None:
        super().__init__(classes="gauge-card")
        self.border_title = title
        self._value = Static("0", classes="gauge-value", markup=True)
        self._bar = ProgressBar(
            total=100, show_eta=False, show_percentage=True, id=gauge_id
        )

    def compose(self) -> ComposeResult:
        yield self._value
        yield self._bar

    def set_gauge(self, value: float, total: float, display: str | None = None) -> None:
        total = max(float(total), 1.0)
        value = max(0.0, min(float(value), total))
        self._bar.update(total=total, progress=value)
        self._value.update(f"[b #7dd3fc]{escape(display or str(int(value)))}[/]")


class BarPanel(Static):
    """Labeled horizontal bars rendered as unicode blocks."""

    def __init__(self, title: str, **kwargs) -> None:
        super().__init__("", markup=True, classes="bar-panel", **kwargs)
        self.border_title = title

    def set_rows(self, rows: list[tuple[str, float, str]]) -> None:
        """rows: (label, value, right-hand caption). Scaled to max value."""
        if not rows:
            self.update("[dim]no data yet[/dim]")
            return
        peak = max((v for _, v, _ in rows), default=0.0) or 1.0
        lines = []
        for label, value, caption in rows:
            lines.append(
                f"[dim]{_short(label):<18}[/dim] {_bar(value, peak)}  {escape(caption)}"
            )
        self.update("\n".join(lines))


class LiveView(VerticalScroll):
    POLL_SECONDS = 2.0

    def __init__(self, client: ApiClient) -> None:
        super().__init__(id="live")
        self.client = client
        self.note = Static("", id="live-note", markup=True)
        self.agent_gauge = GaugeCard("agent working", "gauge-agent")
        self.open_gauge = GaugeCard("open tasks", "gauge-open")
        self.success_gauge = GaugeCard("success %", "gauge-success")
        self.tokens_gauge = GaugeCard("tokens", "gauge-tokens")

        self.tokens_spark = Sparkline([], id="spark-tokens")
        self.duration_spark = Sparkline([], id="spark-duration")
        self.success_spark = Sparkline([], id="spark-success")
        self.wip_spark = Sparkline([], id="spark-wip")

        self.wip_bars = BarPanel("board WIP")
        self.model_bars = BarPanel("models ok / fail")
        self.people_bars = BarPanel("people tokens")
        self.jobs_bars = BarPanel("jobs by status")

        self._fingerprint = ""
        self._wip_ring = RingBuffer(60)
        self._tokens_ring = RingBuffer(60)
        self._duration_ring = RingBuffer(60)
        self._success_ring = RingBuffer(60)
        self._timer = None
        self._seeded = False

    def compose(self) -> ComposeResult:
        yield Label("LIVE", classes="view-head")
        yield Static(
            "Real-time gauges and charts | polls every 2s | owner only",
            classes="view-sub",
        )
        yield self.note
        with Horizontal(id="gauge-row"):
            yield self.agent_gauge
            yield self.open_gauge
            yield self.success_gauge
            yield self.tokens_gauge
        with Horizontal(id="spark-row"):
            with Vertical(classes="spark-panel") as left:
                left.border_title = "tokens / duration / success"
                yield Label("tokens", classes="spark-label")
                yield self.tokens_spark
                yield Label("duration ms", classes="spark-label")
                yield self.duration_spark
                yield Label("success", classes="spark-label")
                yield self.success_spark
                yield Label("open WIP (session)", classes="spark-label")
                yield self.wip_spark
            with Vertical(id="wip-col"):
                yield self.wip_bars
                yield self.jobs_bars
        with Horizontal(id="bar-row"):
            yield self.model_bars
            yield self.people_bars

    # polling -------------------------------------------------------------

    def start_polling(self) -> None:
        self.load()
        if self._timer is None:
            self._timer = self.set_interval(self.POLL_SECONDS, self.load)

    def stop_polling(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    @work(thread=True, exclusive=True, group="live")
    def load(self) -> None:
        try:
            analytics = self.client.analytics()
            series = self.client.metrics_series(limit=60)
            board = self.client.board()
            jobs = self.client.jobs_summary()
            error = ""
        except ApiError as exc:
            analytics, series, board, jobs, error = {}, {}, {}, {}, str(exc)
        self.app.call_from_thread(self._apply, analytics, series, board, jobs, error)

    def _apply(
        self,
        analytics: dict[str, Any],
        series: dict[str, Any],
        board: dict[str, Any],
        jobs: dict[str, Any],
        error: str,
    ) -> None:
        if error:
            self.note.update(f"[red]{escape(error)}[/red]")
            return
        cols = column_counts(board)
        fp = live_fingerprint(analytics, series, cols, jobs)
        # Always refresh rings for WIP so the session sparkline moves.
        open_wip = sum(cols.get(c, 0) for c in BOARD_COLUMNS if c != "done")
        self._wip_ring.append(open_wip)

        if fp == self._fingerprint and self._seeded:
            self.wip_spark.data = self._wip_ring.values() or [0.0]
            return
        self._fingerprint = fp
        self.note.update("")
        self._seeded = True

        summary = analytics.get("summary") or {}
        jobs_done = int(summary.get("jobs_done") or 0)
        jobs_failed = int(summary.get("jobs_failed") or 0)
        jobs_total = max(int(summary.get("jobs_total") or 0), jobs_done + jobs_failed)
        open_tasks = int(summary.get("open_tasks") or 0)
        tokens_total = int(summary.get("tokens_total") or 0)
        agent_working = cols.get("agent_backlog", 0)
        success_pct = (
            round(100.0 * jobs_done / jobs_total) if jobs_total else 0
        )

        self.agent_gauge.set_gauge(
            agent_working, max(agent_working, open_tasks, 5), str(agent_working)
        )
        self.open_gauge.set_gauge(open_tasks, max(open_tasks, 5), str(open_tasks))
        self.success_gauge.set_gauge(success_pct, 100, f"{success_pct}%")
        # Tokens gauge: fill relative to a soft cap that grows with the total.
        token_cap = max(tokens_total, 1000)
        self.tokens_gauge.set_gauge(tokens_total, token_cap, _fmt_int(tokens_total))

        buckets = (series or {}).get("buckets") or {}
        if not self._tokens_ring.values() and buckets.get("tokens"):
            self._tokens_ring.extend(buckets["tokens"])
            self._duration_ring.extend(buckets.get("duration_ms") or [])
            self._success_ring.extend(buckets.get("success_rate") or [])
        elif buckets.get("tokens"):
            # Append the newest point if the series grew.
            self._tokens_ring.append(buckets["tokens"][-1])
            if buckets.get("duration_ms"):
                self._duration_ring.append(buckets["duration_ms"][-1])
            if buckets.get("success_rate"):
                self._success_ring.append(buckets["success_rate"][-1])

        self.tokens_spark.data = self._tokens_ring.values() or [0.0]
        self.duration_spark.data = self._duration_ring.values() or [0.0]
        self.success_spark.data = self._success_ring.values() or [0.0]
        self.wip_spark.data = self._wip_ring.values() or [0.0]

        wip_rows = [
            (status, float(cols.get(status, 0)), str(cols.get(status, 0)))
            for status in BOARD_COLUMNS
        ]
        self.wip_bars.set_rows(wip_rows)

        by_status = (jobs or {}).get("by_status") or {}
        self.jobs_bars.set_rows(
            [(k, float(v), str(v)) for k, v in sorted(by_status.items(), key=lambda kv: -kv[1])]
            or [("none", 0.0, "0")]
        )

        model_rows: list[tuple[str, float, str]] = []
        for m in (analytics.get("models") or [])[:8]:
            ok = int(m.get("success") or 0)
            fail = int(m.get("fail") or 0)
            model_rows.append(
                (
                    str(m.get("model") or "-"),
                    float(ok + fail),
                    f"ok {ok} | fail {fail}",
                )
            )
        self.model_bars.set_rows(model_rows)

        people_rows: list[tuple[str, float, str]] = []
        for p in (analytics.get("people") or [])[:8]:
            tok = int(p.get("tokens") or 0)
            people_rows.append(
                (
                    str(p.get("name") or p.get("email") or "-"),
                    float(tok),
                    _fmt_int(tok),
                )
            )
        self.people_bars.set_rows(people_rows)

    def show_owner_only(self) -> None:
        self.note.update("[yellow]Live charts are owner-only.[/yellow]")

    def snapshot_fingerprint(self) -> str:
        return self._fingerprint
