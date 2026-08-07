"""DeepResearch aio-chart parse/render + marker attach helpers."""

from __future__ import annotations

from app.services.chart_render import (
    charts_marker,
    extract_chart_specs,
    pop_charts_marker,
    process_aio_charts,
    render_chart_png,
)


def test_extract_chart_specs_and_caption():
    text = (
        "## Report\n\nSome findings.\n\n"
        "```aio-chart\n"
        '{"title":"Sales","type":"bar","labels":["A","B"],'
        '"series":[{"name":"Q1","values":[10,20]}]}\n'
        "```\n\n"
        "Done."
    )
    cleaned, specs = extract_chart_specs(text)
    assert len(specs) == 1
    assert specs[0]["title"] == "Sales"
    assert "```aio-chart" not in cleaned
    assert "Chart: Sales" in cleaned
    assert "Done." in cleaned


def test_invalid_chart_json_ignored():
    text = "Hello\n\n```aio-chart\nNOT JSON\n```\n\nBye"
    cleaned, specs = extract_chart_specs(text)
    assert specs == []
    assert "Bye" in cleaned
    assert "```aio-chart" not in cleaned


def test_no_chart_block_unchanged():
    text = "## Only text\n\nNo visuals needed."
    cleaned, specs = extract_chart_specs(text)
    assert specs == []
    assert cleaned == text


def test_render_bar_chart_png():
    chart = render_chart_png(
        {
            "title": "Demo",
            "type": "bar",
            "labels": ["x", "y"],
            "series": [{"name": "s", "values": [1, 2]}],
        }
    )
    assert chart is not None
    assert chart.png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    assert chart.filename.endswith(".png")


def test_render_pie_and_line():
    pie = render_chart_png(
        {"title": "Share", "type": "pie", "labels": ["a", "b"], "values": [30, 70]}
    )
    line = render_chart_png(
        {
            "title": "Trend",
            "type": "line",
            "labels": ["1", "2", "3"],
            "series": [{"name": "v", "values": [1, 3, 2]}],
        }
    )
    assert pie is not None and pie.png_bytes.startswith(b"\x89PNG")
    assert line is not None and line.png_bytes.startswith(b"\x89PNG")


def test_process_aio_charts_roundtrip():
    text = (
        "Intro\n\n```aio-chart\n"
        '{"title":"T","type":"bar","labels":["a"],"series":[{"name":"n","values":[5]}]}\n'
        "```\n"
    )
    cleaned, charts = process_aio_charts(text)
    assert len(charts) == 1
    assert "Chart: T" in cleaned
    assert "```aio-chart" not in cleaned


def test_charts_marker_roundtrip():
    marker = charts_marker([12, 34])
    assert marker == "[[charts:12,34]]"
    body = f"Hello report\n\n{marker}\n[[confirm:9]]"
    cleaned, ids = pop_charts_marker(body)
    assert ids == [12, 34]
    assert "[[charts:" not in cleaned
    assert "Hello report" in cleaned
    assert "[[confirm:9]]" in cleaned


def test_empty_charts_marker():
    assert charts_marker([]) == ""
    cleaned, ids = pop_charts_marker("plain")
    assert cleaned == "plain"
    assert ids == []
