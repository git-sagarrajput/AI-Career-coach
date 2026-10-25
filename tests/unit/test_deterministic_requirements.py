from __future__ import annotations

from career_coach.analysis.deterministic_requirements import deterministic_requirements
from career_coach.ingestion.pipeline import ingest_text
from career_coach.models.enums import DocumentRole, Importance


def test_t43_sections_and_importance(settings, taxonomy, jd_ml_text):
    jd = ingest_text(jd_ml_text, DocumentRole.JOB_DESCRIPTION, settings)
    reqs = deterministic_requirements(jd, taxonomy)
    assert len(reqs) == 9
    assert [r.id for r in reqs][:2] == ["REQ-01", "REQ-02"]
    assert all(r.origin == "deterministic" for r in reqs)
    by_text = {r.text: r for r in reqs}
    assert by_text["Strong SQL skills."].importance == Importance.REQUIRED
    assert by_text["Experience mentoring engineers."].importance == Importance.PREFERRED
    assert (
        "kubernetes"
        in by_text["Hands-on experience with Kubernetes for container orchestration."].taxonomy_skill_ids
    )


def test_t43_bullet_fallback_and_cap(settings, taxonomy):
    text = "Great job\n" + "\n".join(f"- Skill number {i} with Python" for i in range(30))
    jd = ingest_text(text, DocumentRole.JOB_DESCRIPTION, settings)
    reqs = deterministic_requirements(jd, taxonomy)
    assert len(reqs) == 25


def test_duplicates_removed(settings, taxonomy):
    jd = ingest_text("Requirements\n- Python\n- python\n- SQL", DocumentRole.JOB_DESCRIPTION, settings)
    assert [r.text for r in deterministic_requirements(jd, taxonomy)] == ["Python", "SQL"]
