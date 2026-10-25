from __future__ import annotations

import json

import pytest

from career_coach.llm.schemas import AssessmentResponse, CoachingResponse, RequirementExtractionResponse


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


@pytest.mark.parametrize("model", [RequirementExtractionResponse, AssessmentResponse, CoachingResponse])
def test_t53_wire_schema_is_simple(model):
    schema = model.model_json_schema()
    text = json.dumps(schema)
    assert "anyOf" not in text and "oneOf" not in text
    defs = schema.get("$defs", {})
    for name, definition in defs.items():
        assert f'"#/$defs/{name}"' not in json.dumps(definition), f"recursive $ref in {name}"
    for node in _walk(schema):
        if "enum" in node:
            assert all(isinstance(v, str) for v in node["enum"])
