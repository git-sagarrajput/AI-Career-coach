from __future__ import annotations

from career_coach.models.enums import ErrorCategory

__all__ = [
    "CareerCoachError",
    "ConfigurationError",
    "DocumentParseError",
    "ErrorCategory",
    "InputValidationError",
    "LLMAuthenticationError",
    "LLMBudgetExceededError",
    "LLMConfigurationError",
    "LLMError",
    "LLMQuotaError",
    "LLMRequestError",
    "LLMResponseValidationError",
    "LLMTransientError",
    "UnexpectedError",
    "UnsupportedDocumentError",
]


class CareerCoachError(Exception):
    category: ErrorCategory = ErrorCategory.UNEXPECTED
    default_code: str = "error"
    default_message: str = "An error occurred."
    retryable: bool = False

    def __init__(
        self, user_message: str | None = None, *, code: str | None = None, details: str | None = None
    ):
        self.code = code or self.default_code
        self.user_message = user_message or self.default_message
        self.details = details
        super().__init__(f"{self.code}: {self.user_message}")


class ConfigurationError(CareerCoachError):
    category = ErrorCategory.LLM_CONFIGURATION
    default_code = "invalid_configuration"
    default_message = "Invalid configuration."


class InputValidationError(CareerCoachError):
    category = ErrorCategory.USER_INPUT
    default_code = "invalid_input"
    default_message = "The input is invalid."


class UnsupportedDocumentError(CareerCoachError):
    category = ErrorCategory.UNSUPPORTED_DOCUMENT
    default_code = "unsupported_format"
    default_message = "Supported formats: PDF, DOCX (resume); text, PDF, DOCX (job description)."


class DocumentParseError(CareerCoachError):
    category = ErrorCategory.PARSING
    default_code = "parser_failure"
    default_message = (
        "We couldn't read this file. Try re-saving it as a text-based PDF or DOCX and upload it again."
    )


class LLMError(CareerCoachError):
    category = ErrorCategory.UNEXPECTED
    default_code = "llm_error"
    default_message = "The AI analysis failed."


class LLMConfigurationError(LLMError):
    category = ErrorCategory.LLM_CONFIGURATION
    default_code = "missing_api_key"
    default_message = (
        "Gemini API key not configured. Showing deterministic analysis only. See README -> Configure Gemini."
    )


class LLMAuthenticationError(LLMError):
    category = ErrorCategory.LLM_AUTH
    default_code = "auth_error"
    default_message = "The Gemini API key was rejected. Check GEMINI_API_KEY."


class LLMQuotaError(LLMError):
    category = ErrorCategory.LLM_QUOTA
    default_code = "quota_exceeded"
    default_message = (
        "Gemini rate limit or quota reached. Wait and retry later, or check your quota in Google AI Studio."
    )


class LLMTransientError(LLMError):
    category = ErrorCategory.TRANSIENT_NETWORK
    default_code = "unavailable"
    default_message = "Gemini is temporarily unavailable."
    retryable = True


class LLMRequestError(LLMError):
    category = ErrorCategory.LLM_CONFIGURATION
    default_code = "request_rejected"
    default_message = "The Gemini request was rejected."


class LLMResponseValidationError(LLMError):
    category = ErrorCategory.INVALID_MODEL_OUTPUT
    default_code = "schema_invalid"
    default_message = "The AI response could not be validated; showing the parts that could be verified."


class LLMBudgetExceededError(LLMError):
    category = ErrorCategory.TRANSIENT_NETWORK
    default_code = "budget_exceeded"
    default_message = "The AI analysis exceeded its time or attempt budget."


class UnexpectedError(CareerCoachError):
    category = ErrorCategory.UNEXPECTED
    default_code = "unexpected"
    default_message = "Unexpected error. Please try again."
