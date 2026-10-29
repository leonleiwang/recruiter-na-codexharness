"""JD rule-engine tag view; never an input to v5 decisions or ordering."""
from __future__ import annotations

from typing import Any

from rn.resume_screening.analysis.cues import SCORING_DIMENSIONS, canonicalize
from rn.resume_screening.analysis.fields import extract_resume_fields
from rn.resume_screening.analysis.matching import STATE_MATCHED, match_requirements
from rn.resume_screening.analysis.requirements import requirements_from_jd
from rn.resume_screening.analysis.tags import extract_resume_tags, resume_corpus


def _resume_stage_requirements(jd: dict[str, Any], criteria: dict[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
    def key(row: dict[str, Any]) -> tuple[str, str]:
        dimension = str(row.get("dimension") or "")
        name = str(row.get("canonical_name") or row.get("name") or "")
        return dimension, canonicalize(name, dimension=dimension) or name

    # Use approved execution modes only to identify the stage. The requirements
    # and matching evidence still come from the actual JD, never the policy union.
    interview_keys = {key(row) for row in (criteria or {}).get("criteria", [])
                      if row.get("decision_mode") == "interview_verify"}
    doc = requirements_from_jd(jd)
    resume_rows, interview_names = [], []
    for row in doc["requirements"]:
        if row.get("dimension") not in SCORING_DIMENSIONS or not row.get("applicable", True):
            continue
        if key(row) in interview_keys:
            interview_names.append(row["name"])
        else:
            resume_rows.append(row)
    doc["requirements"] = resume_rows
    return doc, interview_names


def jd_tag_groups(jd: dict[str, Any], criteria: dict[str, Any] | None = None) -> dict[str, list[str]]:
    doc, interview_names = _resume_stage_requirements(jd, criteria)
    return {"screening_tag_names": [row["name"] for row in doc["requirements"]],
            "interview_tag_names": interview_names}


def match_jd_card_tags(jd: dict[str, Any], facts: dict[str, Any], criteria: dict[str, Any] | None = None) -> dict[str, Any]:
    """Project only JD tag facts, excluding the engine's auxiliary decision hints.

    Facts are the normalized/redacted resume contract, not AI-derived database
    annotations. Retain rule-engine counted/pending semantics and evidence.
    """
    requirements, interview_names = _resume_stage_requirements(jd, criteria)
    fields = extract_resume_fields(resume_corpus(facts))
    matching = match_requirements(requirements, extract_resume_tags(fields), fields)
    items = [item for dimension in SCORING_DIMENSIONS
             for item in (matching["dimensions"].get(dimension) or {}).get("items", [])]
    names = [item["name"] for item in items
             if item.get("counted", True) and item["state"] == STATE_MATCHED]
    return {
        "source": "resume_screening_jd_rules",
        "jd_id": jd.get("jd_id") or "",
        "stage": "resume",
        "applicable_tag_names": [row["name"] for row in requirements["requirements"]],
        "interview_tag_names": interview_names,
        "matched_tag_names": names,
        "matched_tag_count": matching["tag_hit_count"],
        "applicable_tag_count": matching["tag_applied_count"],
        "items": items,
    }
