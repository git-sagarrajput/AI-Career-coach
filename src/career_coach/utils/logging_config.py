from __future__ import annotations

import contextvars
import logging
import logging.handlers
import re
import sys

analysis_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("analysis_id", default="-")

_KEY_PATTERN = re.compile(r"AIza[0-9A-Za-z_\-]{35}")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\w)(?:\+?\d{1,3}[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]\d{3}[\s.-]\d{4}(?!\w)")
_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] [aid=%(analysis_id)s] %(message)s"
_configured = False


class AnalysisIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.analysis_id = analysis_id_var.get()
        return True


class RedactionFilter(logging.Filter):
    """Removes API keys and contact data from log messages as a last line of defence."""

    def __init__(self, secrets: list[str] | None = None) -> None:
        super().__init__()
        self._secrets = [s for s in (secrets or []) if s]

    def redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "[REDACTED]")
        text = _KEY_PATTERN.sub("[REDACTED]", text)
        text = _EMAIL.sub("[email]", text)
        return _PHONE.sub("[phone]", text)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        record.msg = self.redact(message)
        record.args = None
        return True


def configure_logging(
    level: str = "INFO", secrets: list[str] | None = None, log_file: str | None = None
) -> None:
    global _configured
    if _configured:
        return
    root = logging.getLogger("career_coach")
    root.setLevel(level)
    root.propagate = False
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file:
        handlers.append(logging.handlers.RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=2))
    for handler in handlers:
        handler.setFormatter(logging.Formatter(_FORMAT))
        handler.addFilter(AnalysisIdFilter())
        handler.addFilter(RedactionFilter(secrets))
        root.addHandler(handler)
    for noisy in ("httpx", "httpcore", "google_genai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True
