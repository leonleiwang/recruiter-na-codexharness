"""JD rule-engine tag view; never an input to v5 decisions or ordering."""
from __future__ import annotations

from typing import Any

from rn.resume_screening.analysis.cues import SCORING_DIMENSIONS
from rn.resume_screening.analysis.fields import extract_resume_fields
from rn.resume_screening.analysis.matching import STATE_MATCHED, match_requirements
from rn.resume_screening.analysis.requirements import requirements_from_jd
from rn.resume_screening.analysis.tags import extract_resume_tags, resume_corpus


def jd_tag_names(jd: dict[str, Any]) -> list[str]:
    return [row["name"] for row in requirements_from_jd(jd)["requirements"]
            if row.get("dimension") in SCORING_DIMENSIONS and row.get("applicable", True)]


def match_jd_card_tags(jd: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
    """Project only JD tag facts, excluding the engine's auxiliary decision hints.

    Facts are the normalized/redacted resume contract, not AI-derived database
    annotations. Retain rule-engine counted/pending semantics and evidence.
    """
    requirements = requirements_from_jd(jd)
    fields = extract_resume_fields(resume_corpus(facts))
    matching = match_requirements(requirements, extract_resume_tags(fields), fields)
    items = [item for dimension in SCORING_DIMENSIONS
             for item in (matching["dimensions"].get(dimension) or {}).get("items", [])]
    names = [item["name"] for item in items
             if item.get("counted", True) and item["state"] == STATE_MATCHED]
    return {
        "source": "resume_screening_jd_rules",
        "jd_id": jd.get("jd_id") or "",
        "applicable_tag_names": jd_tag_names(jd),
        "matched_tag_names": names,
        "matched_tag_count": matching["tag_hit_count"],
        "applicable_tag_count": matching["tag_applied_count"],
        "items": items,
    }
