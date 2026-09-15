"""Ground Semantic Evidence quotes to authoritative resume facts."""
from __future__ import annotations

import copy
import re
import unicodedata
from typing import Any

_ALLOWED_ROOTS = (
    "raw_text", "degree", "education", "location", "expected_location",
    "current_company", "current_position", "work_experiences", "projects",
    "certifications", "skills", "industry_tags",
)


def _normalize(text: Any) -> str:
    value = unicodedata.normalize("NFKC", str(text or "")).casefold()
    return re.sub(r"\s+", " ", value).strip()


def _walk(value: Any, path: str, out: dict[str, str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            _walk(item, child, out)
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            _walk(item, f"{path}[{idx}]", out)
    elif value is not None:
        text = str(value).strip()
        if text:
            out[path] = text


def grounding_sources(resume_facts: dict[str, Any]) -> dict[str, str]:
    sources: dict[str, str] = {}
    for root in _ALLOWED_ROOTS:
        if root in resume_facts:
            _walk(resume_facts[root], root, sources)
    return sources


def _ground_quote(quote: str, source_path: str | None, sources: dict[str, str]) -> tuple[bool, str | None]:
    needle = _normalize(quote)
    if not needle:
        return False, None
    if source_path:
        exact = sources.get(str(source_path))
        if exact is not None and needle in _normalize(exact):
            return True, str(source_path)
        # Permit a parent path such as work_experiences[0] while still requiring
        # exact text from a child fact under that path.
        for path, text in sources.items():
            if path.startswith(str(source_path)) and needle in _normalize(text):
                return True, path
        # Models often identify the right verbatim quote but emit a generic or
        # stale source_path. Source labels are metadata; the quote itself is the
        # safety boundary. Resolve the exact quote against every authoritative
        # resume fact and canonicalize the path later instead of rejecting it.
        for path, text in sources.items():
            if needle in _normalize(text):
                return True, path
        return False, None
    for path, text in sources.items():
        if needle in _normalize(text):
            return True, path
    return False, None


def validate_grounding(resume_facts: dict[str, Any], semantic_evidence: dict[str, Any]) -> dict[str, Any]:
    sources = grounding_sources(resume_facts)
    errors: list[str] = []
    details: list[dict[str, Any]] = []

    def check_items(owner: str, items: Any) -> None:
        if not isinstance(items, list):
            return
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                errors.append(f"UNGROUNDED:{owner}:{idx}:INVALID_ITEM")
                continue
            quote = str(item.get("text") or "").strip()
            if not quote:
                errors.append(f"UNGROUNDED:{owner}:{idx}:EMPTY_QUOTE")
                continue
            ok, matched = _ground_quote(quote, item.get("source_path") or item.get("source"), sources)
            details.append({"owner": owner, "index": idx, "text": quote, "grounded": ok, "matched_source_path": matched})
            if not ok:
                errors.append(f"UNGROUNDED:{owner}:{idx}")

    for assessment in semantic_evidence.get("tag_assessments") or []:
        if not isinstance(assessment, dict):
            continue
        level = assessment.get("evidence_level")
        if level not in {"NO_EVIDENCE", "UNASSESSABLE"}:
            check_items(f"tag:{assessment.get('tag_id','')}", assessment.get("evidence", []))

    for assessment in semantic_evidence.get("requirement_assessments") or []:
        if not isinstance(assessment, dict):
            continue
        if assessment.get("status") in {"PASS", "FAIL"}:
            check_items(f"requirement:{assessment.get('requirement_id','')}", assessment.get("evidence", []))

    return {
        "schema_version": "3.1",
        "status": "GROUNDED" if not errors else "GROUNDING_FAILED",
        "errors": errors,
        "checked_items": details,
    }


def validate_decision_grounding(resume_facts: dict[str, Any], decision_evidence: dict[str, Any]) -> dict[str, Any]:
    """Validate v5 Candidate Decision evidence against authoritative resume facts.

    Definitive semantic outcomes must carry exact grounded quotes. Unknown/clear
    states may legitimately have no quote. Any supplied quote is still checked.
    """
    sources = grounding_sources(resume_facts)
    errors: list[str] = []
    details: list[dict[str, Any]] = []

    def check(owner: str, evidence: Any, required: bool = False) -> None:
        rows = evidence if isinstance(evidence, list) else []
        if required and not rows:
            errors.append(f"UNGROUNDED:{owner}:MISSING_EVIDENCE")
            return
        for idx, item in enumerate(rows):
            if not isinstance(item, dict):
                errors.append(f"UNGROUNDED:{owner}:{idx}:INVALID_ITEM")
                continue
            quote = str(item.get("text") or "").strip()
            if not quote:
                errors.append(f"UNGROUNDED:{owner}:{idx}:EMPTY_QUOTE")
                continue
            ok, matched = _ground_quote(quote, item.get("source_path") or item.get("source"), sources)
            details.append({"owner": owner, "index": idx, "text": quote, "grounded": ok, "matched_source_path": matched})
            if not ok:
                errors.append(f"UNGROUNDED:{owner}:{idx}")

    hard = decision_evidence.get("hard_checks") or {}
    for name in ("education", "certification", "location"):
        row = hard.get(name) or {}
        status = str(row.get("status") or "")
        check(f"hard_check:{name}", row.get("evidence") or [], required=bool(status and status != "unknown"))

    for row in decision_evidence.get("red_line_assessments") or []:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "")
        check(f"red_line:{row.get('red_line_id','')}", row.get("evidence") or [], required=status == "hit")

    for row in decision_evidence.get("criteria_assessments") or []:
        if not isinstance(row, dict):
            continue
        result = str(row.get("criterion_result") or "")
        check(
            f"criterion:{row.get('criterion_id','')}",
            row.get("evidence") or [],
            required=result in {"satisfied", "not_satisfied"},
        )

    return {
        "schema_version": "5.0",
        "status": "GROUNDED" if not errors else "GROUNDING_FAILED",
        "errors": errors,
        "checked_items": details,
    }


def repair_decision_grounding(
    resume_facts: dict[str, Any], decision_evidence: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Canonicalize grounded quotes and fail-safe ungrounded model evidence.

    The model is allowed to interpret semantics, but only authoritative resume
    facts may support a definitive outcome. A wrong source label is repaired
    when the exact quote exists elsewhere. If a quote cannot be grounded, the
    affected outcome is downgraded to UNKNOWN instead of turning the whole
    candidate into a technical failure or allowing an unsupported veto.
    """
    repaired = copy.deepcopy(decision_evidence)
    sources = grounding_sources(resume_facts)
    repairs: list[dict[str, Any]] = []

    def grounded_rows(owner: str, evidence: Any) -> list[dict[str, str]]:
        rows = evidence if isinstance(evidence, list) else []
        kept: list[dict[str, str]] = []
        for idx, item in enumerate(rows):
            if not isinstance(item, dict):
                repairs.append({
                    "owner": owner, "index": idx, "action": "dropped_invalid_evidence_item"
                })
                continue
            quote = str(item.get("text") or "").strip()
            if not quote:
                repairs.append({
                    "owner": owner, "index": idx, "action": "dropped_empty_evidence"
                })
                continue
            original_path = str(item.get("source_path") or item.get("source") or "").strip()
            ok, matched = _ground_quote(quote, original_path or None, sources)
            if not ok or not matched:
                repairs.append({
                    "owner": owner, "index": idx, "action": "dropped_ungrounded_evidence",
                    "text": quote, "source_path": original_path,
                })
                continue
            kept.append({"text": quote, "source_path": matched})
            if original_path != matched:
                repairs.append({
                    "owner": owner, "index": idx, "action": "canonicalized_source_path",
                    "from": original_path, "to": matched,
                })
        return kept

    hard = repaired.get("hard_checks") or {}
    for name in ("education", "certification", "location"):
        row = hard.get(name)
        if not isinstance(row, dict):
            continue
        owner = f"hard_check:{name}"
        row["evidence"] = grounded_rows(owner, row.get("evidence") or [])
        status = str(row.get("status") or "unknown")
        if status != "unknown" and not row["evidence"]:
            row["status"] = "unknown"
            repairs.append({
                "owner": owner, "action": "downgraded_hard_check_to_unknown",
                "from_status": status,
            })

    for row in repaired.get("red_line_assessments") or []:
        if not isinstance(row, dict):
            continue
        owner = f"red_line:{row.get('red_line_id','')}"
        row["evidence"] = grounded_rows(owner, row.get("evidence") or [])
        if str(row.get("status") or "unknown") == "hit" and not row["evidence"]:
            row["status"] = "unknown"
            repairs.append({
                "owner": owner, "action": "downgraded_red_line_to_unknown", "from_status": "hit"
            })

    for row in repaired.get("criteria_assessments") or []:
        if not isinstance(row, dict):
            continue
        owner = f"criterion:{row.get('criterion_id','')}"
        row["evidence"] = grounded_rows(owner, row.get("evidence") or [])
        result = str(row.get("criterion_result") or "unknown")
        if result in {"satisfied", "not_satisfied"} and not row["evidence"]:
            row["criterion_result"] = "unknown"
            row["evidence_status"] = "none"
            repairs.append({
                "owner": owner, "action": "downgraded_criterion_to_unknown",
                "from_result": result,
            })
        elif not row["evidence"]:
            # An UNKNOWN criterion may still arrive with a model-authored partial
            # quote. If the quote is not authoritative, erase the evidence signal.
            row["evidence_status"] = "none"

    report = {
        "schema_version": "5.0.2",
        "status": "REPAIRED" if repairs else "UNCHANGED",
        "repair_count": len(repairs),
        "repairs": repairs,
    }
    return repaired, report
