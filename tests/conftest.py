from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from _doc_builders import build_docx, build_pdf  # noqa: E402

from career_coach.analysis.taxonomy import CompiledTaxonomy, load_taxonomy  # noqa: E402
from career_coach.config import Settings, load_settings  # noqa: E402


@pytest.fixture(autouse=True)
def _block_network(request, monkeypatch):
    """Unit/integration tests must not touch the network or a developer's real API key."""
    monkeypatch.setenv("GEMINI_API_KEY", "")
    if request.node.get_closest_marker("live"):
        return

    real_connect = socket.socket.connect
    real_getaddrinfo = socket.getaddrinfo
    loopback = {"127.0.0.1", "::1", "localhost"}

    def guarded_connect(sock, address, *args, **kwargs):
        if isinstance(address, tuple) and address[0] in loopback:
            return real_connect(sock, address, *args, **kwargs)
        raise RuntimeError("Network access is blocked in tests")

    def guarded_getaddrinfo(host, *args, **kwargs):
        if host in loopback or host is None:
            return real_getaddrinfo(host, *args, **kwargs)
        raise RuntimeError("Network access is blocked in tests")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)


@pytest.fixture
def settings() -> Settings:
    return load_settings({})


@pytest.fixture(scope="session")
def taxonomy() -> CompiledTaxonomy:
    return load_taxonomy()


def load_json(relative: str) -> dict:
    return json.loads((FIXTURES / relative).read_text("utf-8"))


@pytest.fixture(scope="session")
def ml_resume_content() -> dict:
    return load_json("resumes/ml_engineer_onnx.json")


@pytest.fixture(scope="session")
def switcher_content() -> dict:
    return load_json("resumes/career_switcher.json")


@pytest.fixture(scope="session")
def ml_resume_docx(ml_resume_content) -> bytes:
    return build_docx(ml_resume_content)


@pytest.fixture(scope="session")
def ml_resume_pdf(ml_resume_content) -> bytes:
    return build_pdf(ml_resume_content)


@pytest.fixture(scope="session")
def jd_ml_text() -> str:
    return (FIXTURES / "jd_ml_engineer.txt").read_text("utf-8")


@pytest.fixture(scope="session")
def jd_analyst_text() -> str:
    return (FIXTURES / "jd_data_analyst.txt").read_text("utf-8")
