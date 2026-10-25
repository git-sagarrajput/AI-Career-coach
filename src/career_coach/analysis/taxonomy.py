from __future__ import annotations

import json
import re
from functools import lru_cache
from importlib import resources
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_BOUNDARY_BEFORE = r"(?<![A-Za-z0-9+#])"
_BOUNDARY_AFTER = r"(?![A-Za-z0-9+#])"


class SkillEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=1)
    category: Literal["language", "framework", "platform", "data", "practice", "soft"]
    aliases: list[str] = []
    case_sensitive_aliases: list[str] = []
    ambiguous: bool = False

    def ci_terms(self) -> list[str]:
        terms = [] if self.name in self.case_sensitive_aliases else [self.name]
        return [t.strip() for t in [*terms, *self.aliases]]


class Taxonomy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    skills: list[SkillEntry]

    @model_validator(mode="after")
    def _validate(self) -> Taxonomy:
        ids = [s.id for s in self.skills]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate skill IDs in taxonomy")
        owner: dict[str, str] = {}
        for skill in self.skills:
            keys = [t.casefold() for t in skill.ci_terms()] + [
                f"cs:{t}" for t in skill.case_sensitive_aliases
            ]
            for key in dict.fromkeys(keys):
                if not key.strip() or key == "cs:":
                    raise ValueError(f"empty alias in skill {skill.id}")
                if key in owner and owner[key] != skill.id:
                    raise ValueError(f"alias '{key}' is shared by {owner[key]} and {skill.id}")
                owner[key] = skill.id
        return self


class CompiledTaxonomy:
    """Taxonomy plus compiled longest-first alternation regexes."""

    def __init__(self, taxonomy: Taxonomy) -> None:
        self.taxonomy = taxonomy
        self.version = taxonomy.version
        self.skills = {s.id: s for s in taxonomy.skills}
        self.ci_lookup: dict[str, str] = {}
        self.cs_lookup: dict[str, str] = {}
        for skill in taxonomy.skills:
            for term in skill.ci_terms():
                self.ci_lookup[term.casefold()] = skill.id
            for term in skill.case_sensitive_aliases:
                self.cs_lookup[term] = skill.id
        self.ci_regex = _alternation(self.ci_lookup.keys(), re.IGNORECASE)
        self.cs_regex = _alternation(self.cs_lookup.keys(), 0)


def _alternation(terms, flags: int) -> re.Pattern[str] | None:
    ordered = sorted(set(terms), key=lambda t: (-len(t), t))
    if not ordered:
        return None
    body = "|".join(re.escape(t) for t in ordered)
    return re.compile(f"{_BOUNDARY_BEFORE}(?:{body}){_BOUNDARY_AFTER}", flags)


def load_taxonomy_data(raw: str | None = None) -> Taxonomy:
    if raw is None:
        raw = (
            resources.files("career_coach.analysis").joinpath("data/skills_taxonomy.json").read_text("utf-8")
        )
    return Taxonomy.model_validate(json.loads(raw))


@lru_cache(maxsize=1)
def load_taxonomy() -> CompiledTaxonomy:
    return CompiledTaxonomy(load_taxonomy_data())
