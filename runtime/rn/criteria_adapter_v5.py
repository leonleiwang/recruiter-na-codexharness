"""Recruiter-Na v5 inspection-technician policy and actual-criteria adapter.

The recruiter-authored v2.3 workflow is treated as an internal business
judgment contract. Hiring managers never edit decision_mode directly.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
_POLICY_PATH = ROOT / "knowledge" / "recruiting" / "policies" / "inspection_technician_v1.json"
_EMPTY_MARKERS = {"", "没有", "无", "暂无", "无额外要求", "没有额外要求", "none", "n/a", "na"}


def load_inspection_technician_policy() -> dict[str, Any]:
    return json.loads(_POLICY_PATH.read_text(encoding="utf-8"))


def _normalize_title(value: Any) -> str:
    text = str(value or "").strip()
    text = text.replace("(", "（").replace(")", "）")
    return re.sub(r"\s+", "", text)


def _request_parts(request: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    payload = request.get("request") or {}
    fields = payload.get("fields") or {}
    match = payload.get("profile_match") or {}
    return payload, fields, match


def supports_inspection_technician(request: dict[str, Any], profile: Any | None = None) -> bool:
    policy = load_inspection_technician_policy()
    _, fields, match = _request_parts(request)
    supported_ids = set(policy.get("supported_profile_ids") or [])
    supported_titles = {_normalize_title(x) for x in (policy.get("supported_titles") or [])}

    profile_id = str(getattr(profile, "profile_id", "") or match.get("profile_id") or "").strip()
    if profile_id in supported_ids:
        return True

    title = _normalize_title(fields.get("position_title") or request.get("position_title"))
    return bool(title and title in supported_titles)


def _split_explicit_requirements(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw = [str(x).strip() for x in value]
    else:
        text = str(value).strip()
        if text.casefold() in _EMPTY_MARKERS:
            return []
        raw = [x.strip() for x in re.split(r"[\n；;]+", text)]
    out: list[str] = []
    for item in raw:
        if item.casefold() in _EMPTY_MARKERS:
            continue
        if item and item not in out:
            out.append(item)
    return out


def build_actual_criteria(request: dict[str, Any], profile: Any | None = None) -> dict[str, Any]:
    if not supports_inspection_technician(request, profile):
        raise ValueError("SCREENING_POLICY_UNSUPPORTED_ROLE")

    policy = load_inspection_technician_policy()
    _, fields, match = _request_parts(request)
    location = str(fields.get("location") or policy["hard_filters"].get("location") or "").strip()
    criteria: list[dict[str, Any]] = []
    counter = 0

    for dimension, key in (("capability", "capabilities"), ("experience", "experiences"), ("trait", "traits")):
        for item in policy.get(key) or []:
            counter += 1
            row = deepcopy(item)
            row.update({"criterion_id": f"C{counter:02d}", "dimension": dimension, "source": "policy"})
            criteria.append(row)

    for extra in _split_explicit_requirements(fields.get("extra_hard_requirements")):
        counter += 1
        criteria.append({
            "criterion_id": f"C{counter:02d}",
            "dimension": "request_extra",
            "name": extra,
            "level": "必须",
            "decision_mode": "hard_gate",
            "description": extra,
            "evidence_hints": "仅以简历中的明确事实判断；未提及或语义不足必须返回 unknown。",
            "source": "hiring_manager_delta",
        })

    hard_filters = deepcopy(policy.get("hard_filters") or {})
    hard_filters["location"] = location
    return {
        "schema_version": "5.0",
        "contract_type": "actual_hiring_criteria",
        "policy_id": policy["policy_id"],
        "request_id": str(request.get("request_id") or ""),
        "role_name": policy["role_name"],
        "matched_profile": {
            "status": str(match.get("status") or ""),
            "profile_id": str(getattr(profile, "profile_id", "") or match.get("profile_id") or ""),
            "profile_name": str(getattr(profile, "name", "") or request.get("profile_name") or ""),
        },
        "position": {
            "name": str(fields.get("position_title") or request.get("position_title") or policy["role_name"]),
            "department": str(fields.get("department") or policy.get("department") or ""),
            "location": location,
            "salary_range": fields.get("salary_range") or "",
            "headcount": fields.get("headcount"),
            "urgency": fields.get("urgency") or "",
        },
        "description": policy.get("description") or "",
        "biz_challenge": list(policy.get("biz_challenge") or []),
        "hard_filters": hard_filters,
        "criteria": criteria,
        "product_visibility": {
            "decision_mode": "internal_only",
            "evidence_hints": "internal_only",
            "manager_input": "request_delta_only",
        },
        "provenance": {
            "business_baseline": policy.get("business_baseline"),
            "policy_version": policy.get("policy_id"),
        },
    }
