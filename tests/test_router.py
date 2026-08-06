from app.router.classify import classify_request, _keyword_fallback


def test_keyword_ask_writing_checklist():
    plan = _keyword_fallback(
        "Research competitor pricing and draft a one-page report, then add follow-up tasks."
    )
    assert plan.agents == ["ask", "writing", "checklist"]


def test_keyword_code_review():
    plan = _keyword_fallback("Please review this diff for bugs")
    assert plan.agents[0] == "code_review"


def test_keyword_checklist_only():
    plan = _keyword_fallback("mark todo done on the checklist")
    assert plan.agents == ["checklist"]


def test_classify_falls_back_without_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    from app.config import get_settings

    get_settings.cache_clear()
    plan = classify_request("Research the market briefly")
    assert "ask" in plan.agents
    assert plan.used_llm is False
