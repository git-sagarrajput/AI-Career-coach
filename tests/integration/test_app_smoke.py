from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[2] / "app.py"


def test_t69_app_smoke(monkeypatch):
    monkeypatch.chdir(APP.parent)
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not at.exception
    assert at.title[0].value.endswith("AI Career Coach")
    assert any("Privacy" in md.value for md in at.markdown)
    analyze = next(b for b in at.button if b.label == "Analyze")
    assert analyze.disabled
