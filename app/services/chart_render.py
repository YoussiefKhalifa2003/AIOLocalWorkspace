"""Parse DeepResearch ```aio-chart``` blocks and render PNG charts."""

from __future__ import annotations

import io
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

CHART_FENCE_RE = re.compile(
    r"```aio-chart\s*\n(.*?)```",
    re.IGNORECASE | re.DOTALL,
)
CHARTS_MARKER_RE = re.compile(r"\n?\[\[charts:([\d,\s]+)\]\]\s*", re.IGNORECASE)

MAX_CHARTS = 3
MAX_POINTS = 40


@dataclass
class RenderedChart:
    title: str
    png_bytes: bytes
    filename: str


def extract_chart_specs(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Remove aio-chart fences; return (cleaned_text, list of raw specs)."""
    specs: list[dict[str, Any]] = []
    captions: list[str] = []

    def repl(match: re.Match[str]) -> str:
        raw = (match.group(1) or "").strip()
        if len(specs) >= MAX_CHARTS:
            return ""
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            logger.info("aio-chart JSON ignored (invalid)")
            return ""
        if not isinstance(data, dict):
            return ""
        title = str(data.get("title") or "Chart").strip() or "Chart"
        specs.append(data)
        captions.append(f"*Chart: {title}*")
        return ""

    cleaned = CHART_FENCE_RE.sub(repl, text or "")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).rstrip()
    if captions:
        cleaned = f"{cleaned}\n\n" + "\n".join(captions)
    return cleaned, specs


def _as_float_list(values: Any) -> list[float]:
    out: list[float] = []
    if not isinstance(values, list):
        return out
    for v in values[:MAX_POINTS]:
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            continue
    return out


def render_chart_png(spec: dict[str, Any]) -> RenderedChart | None:
    """Render one chart spec to PNG bytes. Returns None if unusable."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("matplotlib not installed; skipping chart render")
        return None

    chart_type = str(spec.get("type") or "bar").strip().lower()
    if chart_type not in ("bar", "line", "pie"):
        chart_type = "bar"
    title = str(spec.get("title") or "Chart").strip() or "Chart"
    labels = [str(x) for x in (spec.get("labels") or [])][:MAX_POINTS]
    series_in = spec.get("series") or []
    if not isinstance(series_in, list) or not series_in:
        # allow shorthand values at top level
        vals = _as_float_list(spec.get("values"))
        if not vals:
            return None
        series_in = [{"name": "value", "values": vals}]
        if not labels:
            labels = [str(i + 1) for i in range(len(vals))]

    series: list[tuple[str, list[float]]] = []
    for s in series_in[:6]:
        if not isinstance(s, dict):
            continue
        name = str(s.get("name") or "series").strip() or "series"
        vals = _as_float_list(s.get("values"))
        if vals:
            series.append((name, vals))
    if not series:
        return None

    n = max(len(labels), max(len(v) for _, v in series))
    if not labels:
        labels = [str(i + 1) for i in range(n)]
    labels = labels[:n]
    while len(labels) < n:
        labels.append(str(len(labels) + 1))

    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=120)
    fig.patch.set_facecolor("#1a1d23")
    ax.set_facecolor("#1a1d23")
    colors = ["#6dcc8a", "#7dd3fc", "#fbbf24", "#c4b5fd", "#f472b6", "#94a3b8"]
    text_color = "#e2e8f0"
    ax.tick_params(colors=text_color)
    ax.xaxis.label.set_color(text_color)
    ax.yaxis.label.set_color(text_color)
    ax.title.set_color(text_color)
    for spine in ax.spines.values():
        spine.set_color("#334155")

    try:
        if chart_type == "pie":
            vals = series[0][1][: len(labels)]
            labs = labels[: len(vals)]
            if not vals or sum(abs(v) for v in vals) == 0:
                plt.close(fig)
                return None
            ax.pie(
                vals,
                labels=labs,
                colors=colors[: len(vals)],
                textprops={"color": text_color},
                autopct="%1.0f%%",
            )
            ax.set_title(title, color=text_color, fontsize=13, pad=12)
        elif chart_type == "line":
            x = list(range(len(labels)))
            for i, (name, vals) in enumerate(series):
                y = vals[: len(labels)]
                if len(y) < len(labels):
                    y = y + [float("nan")] * (len(labels) - len(y))
                ax.plot(x, y[: len(labels)], marker="o", label=name, color=colors[i % len(colors)])
            ax.set_xticks(x)
            ax.set_xticklabels(labels, rotation=25, ha="right")
            ax.set_title(title, color=text_color, fontsize=13, pad=12)
            if len(series) > 1:
                leg = ax.legend(facecolor="#1a1d23", edgecolor="#334155")
                for t in leg.get_texts():
                    t.set_color(text_color)
            ax.grid(True, color="#334155", alpha=0.5)
        else:  # bar
            import numpy as np

            x = np.arange(len(labels))
            width = 0.8 / max(len(series), 1)
            for i, (name, vals) in enumerate(series):
                y = vals[: len(labels)]
                if len(y) < len(labels):
                    y = y + [0.0] * (len(labels) - len(y))
                offset = (i - (len(series) - 1) / 2) * width
                ax.bar(x + offset, y, width=width * 0.9, label=name, color=colors[i % len(colors)])
            ax.set_xticks(x)
            ax.set_xticklabels(labels, rotation=25, ha="right")
            ax.set_title(title, color=text_color, fontsize=13, pad=12)
            if len(series) > 1:
                leg = ax.legend(facecolor="#1a1d23", edgecolor="#334155")
                for t in leg.get_texts():
                    t.set_color(text_color)
            ax.grid(True, axis="y", color="#334155", alpha=0.5)

        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", facecolor=fig.get_facecolor(), bbox_inches="tight")
        plt.close(fig)
        data = buf.getvalue()
        if not data:
            return None
        safe = re.sub(r"[^\w.\-]+", "_", title)[:60] or "chart"
        return RenderedChart(title=title, png_bytes=data, filename=f"{safe}.png")
    except Exception:
        logger.exception("chart render failed")
        plt.close(fig)
        return None


def process_aio_charts(text: str) -> tuple[str, list[RenderedChart]]:
    """Extract aio-chart fences, render PNGs, return cleaned markdown + charts."""
    cleaned, specs = extract_chart_specs(text)
    rendered: list[RenderedChart] = []
    for spec in specs:
        chart = render_chart_png(spec)
        if chart:
            rendered.append(chart)
    return cleaned, rendered


def charts_marker(attachment_ids: list[int]) -> str:
    if not attachment_ids:
        return ""
    return f"[[charts:{','.join(str(i) for i in attachment_ids)}]]"


def pop_charts_marker(text: str) -> tuple[str, list[int]]:
    """Strip [[charts:…]] from body; return (clean_text, attachment_ids)."""
    ids: list[int] = []
    body = text or ""

    def repl(match: re.Match[str]) -> str:
        for part in match.group(1).split(","):
            part = part.strip()
            if part.isdigit():
                ids.append(int(part))
        return "\n"

    cleaned = CHARTS_MARKER_RE.sub(repl, body).rstrip()
    return cleaned, ids
