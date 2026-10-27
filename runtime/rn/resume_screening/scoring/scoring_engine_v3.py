#!/usr/bin/env python3
"""Resume Screening Skill v3 deterministic scoring engine.

The engine intentionally does NOT perform semantic matching and contains no
job-specific keyword branches. An upstream Agent/LLM must first produce a
complete Semantic Evidence Contract. Missing or unassessable evidence stops
formal JM/CS scoring instead of silently falling back to keyword matching.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from rn.resume_screening.decision.evidence_grounding import validate_grounding

SCHEMA_VERSION = "3.0"
ENGINE_VERSION = "3.1.0"
EVIDENCE_RATIOS = {
    "QUANTIFIED_CASE": 1.0,
    "CASE": 0.6,
    "WEAK": 0.3,
    "NO_EVIDENCE": 0.0,
}
UNASSESSABLE = "UNASSESSABLE"
REQUIREMENT_STATUSES = {"PASS", "FAIL", "UNKNOWN"}
ACTIVE_APPLICATION_SOURCES = {
    "主动投递",
    "候选人主动投递",
    "direct_application",
    "candidate_application",
}


def _month_index(value: str | date | datetime | None, as_of: date) -> int | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.year * 12 + value.month - 1
    if isinstance(value, date):
        return value.year * 12 + value.month - 1
    text = str(value).strip()
    if not text:
        return None
    if text.lower() in {"至今", "现在", "present", "current", "now"}:
        return as_of.year * 12 + as_of.month - 1
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.year * 12 + dt.month - 1
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m", "%Y/%m", "%Y.%m"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt.year * 12 + dt.month - 1
        except ValueError:
            continue
    return None


def _as_date(value: str | date | datetime | None) -> date:
    if value is None:
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m", "%Y/%m"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported date: {value}")


def _months_between_dates(start: date, end: date) -> int:
    return max(0, (end.year - start.year) * 12 + (end.month - start.month))


def _nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip()) and value.strip() not in {"未提及", "未知", "N/A", "NA"}
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) > 0
    return True


def calc_rq1_stability(resume: dict[str, Any], as_of: str | date | datetime | None = None) -> tuple[int, float | None, str]:
    """Return (score, average_tenure_months, label)."""
    as_of_date = _as_date(as_of)
    stage = str(resume.get("candidate_stage", "")).strip().lower()
    if stage in {"应届生", "实习生", "new_grad", "new graduate", "intern"}:
        return 45, None, "应届/实习适用默认值"

    durations: list[int] = []
    for exp in resume.get("work_experiences") or []:
        start = _month_index(exp.get("start_date"), as_of_date)
        end = _month_index(exp.get("end_date"), as_of_date)
        if start is None or end is None or end < start:
            continue
        durations.append(end - start + 1)

    if not durations:
        return 30, None, "信息不足"

    avg = sum(durations) / len(durations)
    if len(durations) == 1 and avg < 12:
        return 30, round(avg, 1), "单段短经历，信息不足"
    if avg >= 36:
        return 60, round(avg, 1), "极稳"
    if avg >= 24:
        return 50, round(avg, 1), "稳定"
    if avg >= 18:
        return 38, round(avg, 1), "尚可"
    if avg >= 12:
        return 25, round(avg, 1), "偏短"
    if avg >= 6:
        return 12, round(avg, 1), "不稳"
    return 5, round(avg, 1), "高风险"


def calc_rq2_continuity(resume: dict[str, Any], as_of: str | date | datetime | None = None) -> tuple[int, int]:
    """Return (score, total_full_gap_months) from actual experience dates.

    A transition from 2024-01 to 2024-02 has zero full blank months. Overlapping
    experiences are merged so parallel jobs do not create artificial gaps.
    """
    as_of_date = _as_date(as_of)
    intervals: list[tuple[int, int]] = []
    for exp in resume.get("work_experiences") or []:
        start = _month_index(exp.get("start_date"), as_of_date)
        end = _month_index(exp.get("end_date"), as_of_date)
        if start is None or end is None or end < start:
            continue
        intervals.append((start, end))

    if len(intervals) < 2:
        return 20, 0

    intervals.sort()
    merged: list[list[int]] = []
    for start, end in intervals:
        if not merged or start > merged[-1][1] + 1:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)

    gap_months = 0
    for i in range(1, len(merged)):
        previous_end = merged[i - 1][1]
        next_start = merged[i][0]
        gap_months += max(0, next_start - previous_end - 1)

    if gap_months == 0:
        score = 20
    elif gap_months <= 3:
        score = 16
    elif gap_months <= 12:
        score = 12
    elif gap_months <= 24:
        score = 6
    else:
        score = 0
    return score, gap_months


def calc_rq3_completeness(resume: dict[str, Any]) -> tuple[int, int]:
    """Return (score, missing_field_count) using only role-neutral core fields."""
    missing = 0
    for field in ("name", "degree"):
        if not _nonempty(resume.get(field)):
            missing += 1

    experiences = resume.get("work_experiences") or []
    if not experiences:
        missing += 5
    else:
        for exp in experiences:
            if not _nonempty(exp.get("company")):
                missing += 1
            if not _nonempty(exp.get("position")):
                missing += 1
            if not _nonempty(exp.get("start_date")):
                missing += 1
            if not _nonempty(exp.get("end_date")):
                missing += 1
            if not (_nonempty(exp.get("description")) or _nonempty(exp.get("responsibilities"))):
                missing += 1

    if not _nonempty(resume.get("name")) and not experiences:
        return 0, missing
    if missing == 0:
        return 20, 0
    if missing <= 2:
        return 15, missing
    if missing <= 4:
        return 10, missing
    return 5, missing


def calc_rq4_freshness(resume: dict[str, Any], as_of: str | date | datetime | None = None) -> tuple[float, str | None]:
    as_of_date = _as_date(as_of)
    source = str(resume.get("resume_source", resume.get("source", ""))).strip()
    if source in ACTIVE_APPLICATION_SOURCES:
        return 1.0, None

    raw_created = resume.get("resume_created_at", resume.get("created_at"))
    if not _nonempty(raw_created):
        return 1.0, "简历时效未知"
    try:
        created = _as_date(raw_created)
    except ValueError:
        return 1.0, "简历时效未知"

    age_months = _months_between_dates(created, as_of_date)
    if age_months <= 3:
        return 1.0, None
    if age_months <= 6:
        return 0.9, None
    if age_months <= 12:
        return 0.8, "简历较旧"
    return 0.7, "简历可能过时"


def calc_rq(resume: dict[str, Any], as_of: str | date | datetime | None = None) -> dict[str, Any]:
    rq1, avg_tenure, rq1_label = calc_rq1_stability(resume, as_of)
    rq2, gap_months = calc_rq2_continuity(resume, as_of)
    rq3, missing_fields = calc_rq3_completeness(resume)
    freshness, freshness_label = calc_rq4_freshness(resume, as_of)
    raw = rq1 + rq2 + rq3
    score = round(raw * freshness, 1)
    return {
        "score": score,
        "raw_score": raw,
        "rq1_stability": rq1,
        "rq1_label": rq1_label,
        "average_tenure_months": avg_tenure,
        "rq2_continuity": rq2,
        "career_gap_months": gap_months,
        "rq3_completeness": rq3,
        "missing_core_fields": missing_fields,
        "rq4_freshness_multiplier": freshness,
        "rq4_freshness_label": freshness_label,
    }


def _failure(status: str, resume: dict[str, Any] | None, job: dict[str, Any] | None, errors: list[str]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "scoring_status": status,
        "candidate_id": (resume or {}).get("candidate_id"),
        "job_id": (job or {}).get("job_id"),
        "errors": sorted(set(errors)),
        "rq": None,
        "jm": None,
        "cs": None,
        "veto": False,
        "veto_reasons": [],
        "needs_verification": [],
        "recommendation_level": None,
    }


def _validate_job(job: dict[str, Any] | None) -> list[str]:
    errors: list[str] = []
    if not isinstance(job, dict):
        return ["JOB_CONTRACT_MISSING"]
    if str(job.get("schema_version")) != SCHEMA_VERSION:
        errors.append("JOB_SCHEMA_VERSION_INVALID")
    if not _nonempty(job.get("job_id")):
        errors.append("JOB_ID_MISSING")
    tags = job.get("tags")
    if not isinstance(tags, list) or not tags:
        errors.append("TAGS_MISSING")
        return errors
    seen: set[str] = set()
    total_weight = 0.0
    for tag in tags:
        tag_id = str(tag.get("tag_id", "")).strip()
        if not tag_id:
            errors.append("TAG_ID_MISSING")
        elif tag_id in seen:
            errors.append("TAG_ID_DUPLICATE")
        seen.add(tag_id)
        try:
            weight = float(tag.get("weight"))
        except (TypeError, ValueError):
            errors.append("TAG_WEIGHT_INVALID")
            continue
        if weight <= 0:
            errors.append("TAG_WEIGHT_INVALID")
        total_weight += weight
    if abs(total_weight - 1.0) > 0.001:
        errors.append("TAG_WEIGHT_SUM_INVALID")

    reqs = job.get("requirements", [])
    if not isinstance(reqs, list):
        errors.append("REQUIREMENTS_INVALID")
    else:
        req_ids: set[str] = set()
        for req in reqs:
            req_id = str(req.get("requirement_id", "")).strip()
            if not req_id:
                errors.append("REQUIREMENT_ID_MISSING")
            elif req_id in req_ids:
                errors.append("REQUIREMENT_ID_DUPLICATE")
            req_ids.add(req_id)
            if req.get("gate_type") not in {"HARD_GATE", "VERIFY_ONLY"}:
                errors.append("GATE_TYPE_INVALID")
    return errors


def _validate_evidence(
    resume: dict[str, Any], job: dict[str, Any], evidence: dict[str, Any] | None
) -> tuple[str | None, list[str]]:
    """Return (failure_status, errors). None status means valid evidence."""
    if evidence is None:
        return "EVIDENCE_EVALUATION_FAILED", ["SEMANTIC_EVIDENCE_MISSING"]
    if not isinstance(evidence, dict):
        return "CONTRACT_INVALID", ["SEMANTIC_EVIDENCE_CONTRACT_INVALID"]

    contract_errors: list[str] = []
    evidence_errors: list[str] = []
    if str(evidence.get("schema_version")) != SCHEMA_VERSION:
        contract_errors.append("EVIDENCE_SCHEMA_VERSION_INVALID")
    if evidence.get("candidate_id") != resume.get("candidate_id"):
        contract_errors.append("CANDIDATE_ID_MISMATCH")
    if evidence.get("job_id") != job.get("job_id"):
        contract_errors.append("JOB_ID_MISMATCH")
    if evidence.get("evaluation_status") != "COMPLETE":
        evidence_errors.append("EVALUATION_NOT_COMPLETE")

    assessments = evidence.get("tag_assessments")
    if not isinstance(assessments, list):
        return "CONTRACT_INVALID", contract_errors + ["TAG_ASSESSMENTS_INVALID"]

    expected_tags = {str(t["tag_id"]): t for t in job["tags"]}
    actual_tags: dict[str, dict[str, Any]] = {}
    for assessment in assessments:
        tag_id = str(assessment.get("tag_id", ""))
        if tag_id in actual_tags:
            contract_errors.append("TAG_ASSESSMENT_DUPLICATE")
        actual_tags[tag_id] = assessment

    if set(actual_tags) != set(expected_tags):
        evidence_errors.append("TAG_ASSESSMENT_COVERAGE_INCOMPLETE")

    for tag_id, assessment in actual_tags.items():
        if tag_id not in expected_tags:
            continue
        level = assessment.get("evidence_level")
        if level == UNASSESSABLE:
            evidence_errors.append("UNASSESSABLE_TAG_PRESENT")
            continue
        if level not in EVIDENCE_RATIOS:
            contract_errors.append("EVIDENCE_LEVEL_INVALID")
            continue
        confidence = assessment.get("confidence")
        try:
            confidence_value = float(confidence)
            if confidence_value < 0 or confidence_value > 1:
                raise ValueError
        except (TypeError, ValueError):
            contract_errors.append("CONFIDENCE_INVALID")
        if not _nonempty(assessment.get("reasoning_summary")):
            contract_errors.append("REASONING_SUMMARY_REQUIRED")
        evidence_items = assessment.get("evidence", [])
        if not isinstance(evidence_items, list):
            contract_errors.append("EVIDENCE_LIST_INVALID")
            continue
        if level != "NO_EVIDENCE":
            texts = [item.get("text") for item in evidence_items if isinstance(item, dict)]
            if not any(_nonempty(text) for text in texts):
                evidence_errors.append("EVIDENCE_REQUIRED")

    expected_reqs = {str(r["requirement_id"]): r for r in job.get("requirements", [])}
    req_assessments = evidence.get("requirement_assessments", [])
    if not isinstance(req_assessments, list):
        contract_errors.append("REQUIREMENT_ASSESSMENTS_INVALID")
    else:
        actual_reqs: dict[str, dict[str, Any]] = {}
        for assessment in req_assessments:
            req_id = str(assessment.get("requirement_id", ""))
            if req_id in actual_reqs:
                contract_errors.append("REQUIREMENT_ASSESSMENT_DUPLICATE")
            actual_reqs[req_id] = assessment
        if set(actual_reqs) != set(expected_reqs):
            evidence_errors.append("REQUIREMENT_ASSESSMENT_COVERAGE_INCOMPLETE")
        for req_id, assessment in actual_reqs.items():
            if req_id not in expected_reqs:
                continue
            status = assessment.get("status")
            if status not in REQUIREMENT_STATUSES:
                contract_errors.append("REQUIREMENT_STATUS_INVALID")
                continue
            if not _nonempty(assessment.get("reasoning_summary")):
                contract_errors.append("REQUIREMENT_REASONING_REQUIRED")
            if status in {"PASS", "FAIL"}:
                items = assessment.get("evidence", [])
                texts = [item.get("text") for item in items if isinstance(item, dict)] if isinstance(items, list) else []
                if not any(_nonempty(text) for text in texts):
                    evidence_errors.append("REQUIREMENT_EVIDENCE_REQUIRED")

    if contract_errors:
        return "CONTRACT_INVALID", contract_errors + evidence_errors
    if evidence_errors:
        return "EVIDENCE_EVALUATION_FAILED", evidence_errors
    return None, []


def _evaluate_requirements(job: dict[str, Any], evidence: dict[str, Any]) -> tuple[bool, list[str], list[str], list[dict[str, Any]]]:
    by_id = {str(a["requirement_id"]): a for a in evidence.get("requirement_assessments", [])}
    veto_reasons: list[str] = []
    needs_verification: list[str] = []
    details: list[dict[str, Any]] = []
    for req in job.get("requirements", []):
        req_id = str(req["requirement_id"])
        assessment = by_id[req_id]
        status = assessment["status"]
        gate_type = req["gate_type"]
        if status == "UNKNOWN":
            needs_verification.append(req_id)
        if gate_type == "HARD_GATE" and status == "FAIL":
            veto_reasons.append(req_id)
        details.append(
            {
                "requirement_id": req_id,
                "name": req.get("name", req_id),
                "gate_type": gate_type,
                "status": status,
                "reasoning_summary": assessment.get("reasoning_summary"),
                "evidence": assessment.get("evidence", []),
            }
        )
    return bool(veto_reasons), veto_reasons, needs_verification, details


def _calc_jm(job: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    by_id = {str(a["tag_id"]): a for a in evidence["tag_assessments"]}
    total = 0.0
    details: list[dict[str, Any]] = []
    for tag in job["tags"]:
        tag_id = str(tag["tag_id"])
        assessment = by_id[tag_id]
        level = assessment["evidence_level"]
        ratio = EVIDENCE_RATIOS[level]
        weight = float(tag["weight"])
        contribution = ratio * weight * 100
        total += contribution
        details.append(
            {
                "tag_id": tag_id,
                "name": tag.get("name", tag_id),
                "category": tag.get("category", "other"),
                "weight": weight,
                "evidence_level": level,
                "ratio": ratio,
                "contribution": round(contribution, 2),
                "confidence": float(assessment["confidence"]),
                "reasoning_summary": assessment.get("reasoning_summary"),
                "evidence": assessment.get("evidence", []),
            }
        )
    return {"score": round(total, 1), "tag_details": details, "multiplier": 1.0}


def recommendation_level(cs: float | int, veto: bool = False) -> str:
    if veto:
        return "VETO"
    if cs >= 75:
        return "STRONG_RECOMMEND"
    if cs >= 55:
        return "RECOMMEND"
    if cs >= 35:
        return "PENDING"
    return "WEAK"


def score_candidate(
    resume: dict[str, Any] | None,
    job: dict[str, Any] | None,
    semantic_evidence: dict[str, Any] | None,
    *,
    as_of: str | date | datetime | None = None,
) -> dict[str, Any]:
    """Validate contracts and calculate deterministic RQ/JM/CS.

    Formal JM/CS are never emitted when the Semantic Evidence Contract is
    absent, incomplete, unassessable, or structurally invalid.
    """
    if not isinstance(resume, dict):
        return _failure("PARSING_FAILED", resume, job, ["RESUME_FACTS_MISSING"])
    if not _nonempty(resume.get("candidate_id")):
        return _failure("PARSING_FAILED", resume, job, ["CANDIDATE_ID_MISSING"])

    job_errors = _validate_job(job)
    if job_errors:
        return _failure("CONTRACT_INVALID", resume, job, job_errors)
    assert isinstance(job, dict)

    evidence_status, evidence_errors = _validate_evidence(resume, job, semantic_evidence)
    if evidence_status:
        return _failure(evidence_status, resume, job, evidence_errors)
    assert isinstance(semantic_evidence, dict)

    grounding = validate_grounding(resume, semantic_evidence)
    if grounding["status"] != "GROUNDED":
        return _failure("EVIDENCE_GROUNDING_FAILED", resume, job, grounding["errors"])

    rq = calc_rq(resume, as_of)
    jm = _calc_jm(job, semantic_evidence)
    veto, veto_reasons, needs_verification, requirement_details = _evaluate_requirements(job, semantic_evidence)
    cs = -1 if veto else round(rq["score"] * 0.3 + jm["score"] * 0.7, 1)

    return {
        "schema_version": SCHEMA_VERSION,
        "scoring_status": "SCORING_COMPLETE",
        "candidate_id": resume.get("candidate_id"),
        "job_id": job.get("job_id"),
        "errors": [],
        "rq": rq,
        "jm": jm,
        "cs": cs,
        "veto": veto,
        "veto_reasons": veto_reasons,
        "needs_verification": needs_verification,
        "requirement_details": requirement_details,
        "recommendation_level": recommendation_level(cs, veto),
    }


def _read_json(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def main() -> int:
    parser = argparse.ArgumentParser(description="Resume Screening Skill v3 deterministic scorer")
    parser.add_argument("--resume", required=True, help="resume_facts JSON")
    parser.add_argument("--job", required=True, help="job_contract JSON")
    parser.add_argument("--evidence", required=True, help="semantic_evidence JSON")
    parser.add_argument("--output", help="write result JSON to this path; stdout if omitted")
    parser.add_argument("--as-of", help="scoring date YYYY-MM-DD")
    args = parser.parse_args()

    result = score_candidate(
        _read_json(args.resume),
        _read_json(args.job),
        _read_json(args.evidence),
        as_of=args.as_of,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0 if result["scoring_status"] == "SCORING_COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
