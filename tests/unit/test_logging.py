from __future__ import annotations

import logging

from career_coach.utils.logging_config import AnalysisIdFilter, RedactionFilter, analysis_id_var

KEY = "AIza" + "B" * 35


def _record(msg, *args):
    return logging.LogRecord("career_coach.test", logging.INFO, __file__, 1, msg, args, None)


def test_t63_redaction_and_analysis_id():
    record = _record("calling with key=%s other=%s mail=%s", "my-secret-value", KEY, "jane@example.com")
    RedactionFilter(["my-secret-value"]).filter(record)
    message = record.getMessage()
    assert "my-secret-value" not in message and KEY not in message and "jane@example.com" not in message
    assert message.count("[REDACTED]") == 2

    token = analysis_id_var.set("abc123def456")
    try:
        record = _record("hello")
        AnalysisIdFilter().filter(record)
        assert record.analysis_id == "abc123def456"
    finally:
        analysis_id_var.reset(token)
    record = _record("hello")
    AnalysisIdFilter().filter(record)
    assert record.analysis_id == "-"
