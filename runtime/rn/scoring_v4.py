"""Deterministic v4 blend for ProfileMatch + JDMatch + RQ.

LLMs never calculate these weights or final scores. They only produce grounded
semantic evidence consumed by the existing deterministic component scorer.
"""
from __future__ import annotations

from typing import Any

PROFILE_WEIGHT = 0.40
JD_WEIGHT_WITH_PROFILE = 0.60
RQ_WEIGHT = 0.30
JM_WEIGHT = 0.70


def combine_component_scores(*, rq_score: float, profile_score: float | None, jd_score: float,
                             profile_matched: bool, veto: bool) -> dict[str, Any]:
    rq = float(rq_score)
    jd = float(jd_score)
    if profile_matched:
        if profile_score is None:
            raise ValueError("PROFILE_SCORE_REQUIRED_WHEN_MATCHED")
        pw, jw = PROFILE_WEIGHT, JD_WEIGHT_WITH_PROFILE
        jm = float(profile_score) * pw + jd * jw
    else:
        pw, jw = 0.0, 1.0
        jm = jd
    jm = round(jm, 1)
    cs = -1 if veto else round(rq * RQ_WEIGHT + jm * JM_WEIGHT, 1)
    return {
        "profile_weight": pw,
        "jd_weight": jw,
        "rq_weight": RQ_WEIGHT,
        "jm_weight": JM_WEIGHT,
        "jm": jm,
        "cs": cs,
    }
