from app.api.objectives import _bar


def test_progress_bar_empty():
    assert _bar(0, 0).endswith("0%")


def test_progress_bar_half():
    assert _bar(1, 2, width=4) == "[##--] 50%"


def test_progress_bar_full():
    assert _bar(3, 3, width=10) == "[##########] 100%"
