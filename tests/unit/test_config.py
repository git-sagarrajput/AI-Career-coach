from __future__ import annotations

import pytest

from career_coach.config import load_settings
from career_coach.errors import ConfigurationError


def test_t62_defaults_without_key():
    s = load_settings({})
    assert s.gemini_api_key is None
    assert not s.llm_configured
    assert s.gemini_model == "gemini-3.8-flash"
    assert s.max_upload_mb == 5 and s.link_workers == 5 and s.link_max_urls == 15


def test_key_is_secret():
    s = load_settings({"GEMINI_API_KEY": "AIza" + "x" * 35})
    assert s.llm_configured
    assert "AIza" not in repr(s)


@pytest.mark.parametrize(
    ("env", "var"),
    [
        ({"CAREER_COACH_MAX_PAGES": "abc"}, "CAREER_COACH_MAX_PAGES"),
        ({"CAREER_COACH_LINK_WORKERS": "99"}, "CAREER_COACH_LINK_WORKERS"),
        ({"CAREER_COACH_OCR_ENABLED": "maybe"}, "CAREER_COACH_OCR_ENABLED"),
        ({"GEMINI_MAX_RETRIES": "7"}, "GEMINI_MAX_RETRIES"),
    ],
)
def test_t62_invalid_values_name_the_variable(env, var):
    with pytest.raises(ConfigurationError) as exc:
        load_settings(env)
    assert var in exc.value.user_message


def test_bool_and_level_parsing():
    s = load_settings({"CAREER_COACH_LINK_CHECK_ENABLED": "TRUE", "CAREER_COACH_LOG_LEVEL": "debug"})
    assert s.link_check_enabled is True
    assert s.log_level == "DEBUG"
