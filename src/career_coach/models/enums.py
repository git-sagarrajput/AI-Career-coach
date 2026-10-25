from __future__ import annotations

from enum import StrEnum


class DocumentRole(StrEnum):
    RESUME = "resume"
    JOB_DESCRIPTION = "job_description"


class SourceFormat(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    TEXT = "text"


class SectionKind(StrEnum):
    CONTACT = "contact"
    SUMMARY = "summary"
    SKILLS = "skills"
    EXPERIENCE = "experience"
    PROJECTS = "projects"
    EDUCATION = "education"
    CERTIFICATIONS = "certifications"
    ABOUT = "about"
    RESPONSIBILITIES = "responsibilities"
    REQUIREMENTS = "requirements"
    PREFERRED = "preferred"
    BENEFITS = "benefits"
    OTHER = "other"


class SectionDetection(StrEnum):
    STYLE = "style"
    LEXICON = "lexicon"
    NONE = "none"


class BlockKind(StrEnum):
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE_ROW = "table_row"


class SpanOrigin(StrEnum):
    NATIVE = "native"
    OCR = "ocr"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class LinkSyntaxStatus(StrEnum):
    VALID = "valid"
    INVALID_SYNTAX = "invalid_syntax"
    UNSUPPORTED_SCHEME = "unsupported_scheme"
    NOT_WEB_LINK = "not_web_link"
    BLOCKED_BY_POLICY = "blocked_by_policy"


class ReachabilityStatus(StrEnum):
    NOT_CHECKED = "not_checked"
    REACHABLE = "reachable"
    RESPONDED_NON_SUCCESS = "responded_non_success"
    REDIRECT_LIMIT = "redirect_limit"
    TIMEOUT = "timeout"
    DNS_FAILURE = "dns_failure"
    CONNECTION_ERROR = "connection_error"
    BLOCKED_BY_POLICY = "blocked_by_policy"
    SKIPPED_LIMIT = "skipped_limit"
    SKIPPED_DEADLINE = "skipped_deadline"
    ERROR = "error"


class RequirementCategory(StrEnum):
    TECHNICAL_SKILL = "technical_skill"
    TOOL_OR_PLATFORM = "tool_or_platform"
    DOMAIN_KNOWLEDGE = "domain_knowledge"
    EXPERIENCE = "experience"
    EDUCATION = "education"
    CERTIFICATION = "certification"
    SOFT_SKILL = "soft_skill"
    RESPONSIBILITY = "responsibility"
    OTHER = "other"


class Importance(StrEnum):
    REQUIRED = "required"
    PREFERRED = "preferred"
    UNSPECIFIED = "unspecified"


class ImportanceSource(StrEnum):
    JD_CUE = "jd_cue"
    MODEL = "model"
    DEFAULT = "default"


class AlignmentClassification(StrEnum):
    SUPPORTED = "supported"
    PARTIAL = "partial"
    AMBIGUOUS = "ambiguous"
    NOT_EVIDENCED = "not_evidenced"


class AssessmentMethod(StrEnum):
    SEMANTIC_LLM = "semantic_llm"
    TAXONOMY = "taxonomy"
    DETERMINISTIC_KEYWORD = "deterministic_keyword"


class Seniority(StrEnum):
    UNSPECIFIED = "unspecified"
    ENTRY = "entry"
    MID = "mid"
    SENIOR = "senior"
    LEAD = "lead"
    MANAGER = "manager"


class Effort(StrEnum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


class ReportStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    DETERMINISTIC_ONLY = "deterministic_only"


class ErrorCategory(StrEnum):
    USER_INPUT = "user_input"
    UNSUPPORTED_DOCUMENT = "unsupported_document"
    PARSING = "parsing"
    PARTIAL_EXTRACTION = "partial_extraction"
    URL_VALIDATION = "url_validation"
    LLM_CONFIGURATION = "llm_configuration"
    LLM_AUTH = "llm_auth"
    LLM_QUOTA = "llm_quota"
    TRANSIENT_NETWORK = "transient_network"
    INVALID_MODEL_OUTPUT = "invalid_model_output"
    UNEXPECTED = "unexpected"


class LLMStatus(StrEnum):
    OK = "ok"
    NOT_CONFIGURED = "not_configured"
    AUTH_ERROR = "auth_error"
    MODEL_NOT_FOUND = "model_not_found"
    QUOTA_EXCEEDED = "quota_exceeded"
    UNAVAILABLE = "unavailable"
    INVALID_OUTPUT = "invalid_output"
    BUDGET_EXCEEDED = "budget_exceeded"
