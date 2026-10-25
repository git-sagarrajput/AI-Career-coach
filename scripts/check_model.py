"""Verify that GEMINI_MODEL is available for the configured key. Sends no document data.

Usage: python scripts/check_model.py
Exit code 0 when the model exists and supports generateContent, 1 otherwise.
"""

from __future__ import annotations

import sys

from career_coach.config import load_settings
from career_coach.errors import CareerCoachError
from career_coach.llm.client import map_sdk_error


def main() -> int:
    try:
        settings = load_settings()
    except CareerCoachError as exc:
        print(f"Configuration error: {exc.user_message}")
        return 1
    if not settings.llm_configured:
        print("GEMINI_API_KEY is not set. Add it to .env (see README -> Configure Gemini).")
        return 1

    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=settings.gemini_api_key.get_secret_value(),  # type: ignore[union-attr]
        http_options=types.HttpOptions(timeout=30_000, retry_options=types.HttpRetryOptions(attempts=1)),
    )
    name = settings.gemini_model
    try:
        model = client.models.get(model=name)
    except Exception as exc:  # noqa: BLE001
        mapped = map_sdk_error(exc, name)
        print(f"Model check failed for '{name}': {mapped.user_message}")
        return 1
    actions = [a.lower() for a in (getattr(model, "supported_actions", None) or [])]
    supports = not actions or any("generatecontent" in a for a in actions)
    print(f"Model:            {getattr(model, 'name', name)}")
    print(f"Display name:     {getattr(model, 'display_name', '') or '-'}")
    print(f"Input tokens:     {getattr(model, 'input_token_limit', '-')}")
    print(f"Output tokens:    {getattr(model, 'output_token_limit', '-')}")
    print(f"generateContent:  {'yes' if supports else 'NO'}")
    return 0 if supports else 1


if __name__ == "__main__":
    sys.exit(main())
