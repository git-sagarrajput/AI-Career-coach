from __future__ import annotations

import json

import pytest

from career_coach.analysis.taxonomy import load_taxonomy_data


def test_shipped_taxonomy_is_valid(taxonomy):
    assert taxonomy.version
    assert len(taxonomy.skills) >= 100


def _raw(skills):
    return json.dumps({"version": "t", "skills": skills})


def test_t38_duplicate_alias_rejected():
    skills = [
        {"id": "a", "name": "Alpha", "category": "language", "aliases": ["shared"]},
        {"id": "b", "name": "Beta", "category": "language", "aliases": ["Shared"]},
    ]
    with pytest.raises(ValueError, match="shared"):
        load_taxonomy_data(_raw(skills))


def test_duplicate_ids_rejected():
    skills = [
        {"id": "a", "name": "Alpha", "category": "language"},
        {"id": "a", "name": "Beta", "category": "data"},
    ]
    with pytest.raises(ValueError):
        load_taxonomy_data(_raw(skills))


def test_empty_alias_rejected():
    with pytest.raises(ValueError):
        load_taxonomy_data(_raw([{"id": "a", "name": "Alpha", "category": "language", "aliases": ["  "]}]))
