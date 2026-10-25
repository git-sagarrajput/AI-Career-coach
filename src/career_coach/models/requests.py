from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from career_coach.models.enums import Seniority

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class UploadedInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    filename: str = Field(max_length=255)
    data: bytes = Field(repr=False)


class UserPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    target_role: str | None = Field(default=None, max_length=100)
    seniority: Seniority = Seniority.UNSPECIFIED
    focus_areas: str | None = Field(default=None, max_length=200)

    @field_validator("target_role", "focus_areas", mode="before")
    @classmethod
    def _clean(cls, value: object) -> object:
        if isinstance(value, str):
            value = _CONTROL.sub("", value).strip()
            return value or None
        return value


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resume: UploadedInput
    jd_text: str | None = Field(default=None, repr=False)
    jd_file: UploadedInput | None = None
    preferences: UserPreferences = UserPreferences()
    check_links: bool = False

    @model_validator(mode="after")
    def _need_jd(self) -> AnalysisRequest:
        if not (self.jd_text and self.jd_text.strip()) and self.jd_file is None:
            raise ValueError("A job description (text or file) is required.")
        return self


class ProgressEvent(BaseModel):
    stage: Literal[
        "validate",
        "parse_resume",
        "parse_jd",
        "sections",
        "links",
        "requirements",
        "assessment",
        "coaching",
        "report",
    ]
    status: Literal["started", "completed", "skipped", "failed"]
    message: str = Field(max_length=200)
