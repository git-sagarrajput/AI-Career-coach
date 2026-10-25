"""T-70: import-boundary rules (spec 8.2) and excluded technologies."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "career_coach"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text("utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _modules():
    for path in SRC.rglob("*.py"):
        yield path.relative_to(SRC).as_posix(), _imports(path)


def _uses(imports: set[str], prefix: str) -> bool:
    return any(name == prefix or name.startswith(prefix + ".") for name in imports)


def test_t70_import_boundaries():
    for rel, imports in _modules():
        assert not _uses(imports, "streamlit"), f"{rel} imports streamlit"
        if _uses(imports, "google.genai") or _uses(imports, "google"):
            assert rel == "llm/client.py", f"{rel} imports google.genai"
        if _uses(imports, "httpx"):
            assert rel in ("links/validation.py", "llm/client.py", "analysis/orchestrator.py"), rel
        if _uses(imports, "pymupdf"):
            assert rel.startswith("ingestion/"), rel
        if _uses(imports, "docx"):
            assert rel == "ingestion/docx_parser.py", rel
        if rel.startswith("models/"):
            allowed = ("pydantic", "career_coach.models", "__future__", "datetime", "enum", "typing", "re")
            assert all(any(i == a or i.startswith(a + ".") for a in allowed) for i in imports), (rel, imports)
        if rel.startswith("llm/"):
            assert not _uses(imports, "career_coach.ingestion"), rel


def test_app_is_only_streamlit_importer():
    assert _uses(_imports(ROOT / "app.py"), "streamlit")


def test_no_excluded_technologies():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    deps = " ".join(project["project"]["dependencies"]).lower()
    for banned in (
        "redis",
        "celery",
        "langchain",
        "chromadb",
        "faiss",
        "pinecone",
        "fastapi",
        "flask",
        "docker",
        "kafka",
        "google-generativeai",
        "pdfplumber",
    ):
        assert banned not in deps, banned
