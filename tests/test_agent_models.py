"""Agent model prefs + OpenCode catalog."""

from app.services.agent_models import resolve_agent_model
from app.services.opencode_provider import is_opencode_model, list_free_models, normalize_model_id
from app.services.openrouter_provider import is_openrouter_model, list_openrouter_free_models


def test_free_models_catalog():
    models = list_free_models()
    ids = {m["id"] for m in models}
    assert "big-pickle" in ids
    curated = list_openrouter_free_models()
    assert any(m["id"].endswith(":free") or m["id"] == "openrouter/free" for m in curated)


def test_normalize_and_detect():
    assert normalize_model_id("opencode/big-pickle") == "big-pickle"
    assert is_opencode_model("big-pickle")
    assert is_openrouter_model("qwen/qwen3-coder:free")
    assert not is_openrouter_model("gemini-env")


def test_resolve_prefers_payload(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/m.db")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("OPENCODE_API_KEY", "")
    from app.config import get_settings

    get_settings.cache_clear()
    import app.db.session as sess
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.models import Base

    engine = create_engine(f"sqlite:///{tmp_path}/m.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sess.engine = engine
    sess.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = sess.SessionLocal()
    mid, backend = resolve_agent_model(
        db, tenant_id=1, agent_type="coding", payload_model="qwen/qwen3-coder:free"
    )
    assert mid == "qwen/qwen3-coder:free"
    assert backend == "openrouter"
    mid2, backend2 = resolve_agent_model(
        db, tenant_id=1, agent_type="coding", payload_model="gemini-env"
    )
    assert backend2 == "gemini"
    db.close()
