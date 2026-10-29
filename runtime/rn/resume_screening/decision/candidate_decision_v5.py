"""Deterministic Candidate Decision engine for Recruiter-Na v5."""
from __future__ import annotations

from typing import Any

_HARD_LABELS = {
    "education": "学历",
    "certification": "检验资格证",
    "location": "工作地点",
}

# 只有能力/经验判据进入经理端「符合标签 x/y」的分子与分母：
# - 学历/资格证/工作地点是资格门槛，单独一行展示，不算标签；
# - 特质（抗压/细心/责任心/积极主动/持续学习）只能在面试中确认，
#   按 AGENTS.md 只进「面试重点」，不能当成推荐理由或命中标签。
_COUNTED_DIMENSIONS = {"capability", "experience"}


def _counted_criteria(criteria: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Capability/experience criteria applicable to this profile, in policy order."""
    return {
        str(row.get("criterion_id") or ""): row
        for row in criteria.get("criteria") or []
        if str(row.get("dimension") or "") in _COUNTED_DIMENSIONS
    }


def applicable_tag_names(criteria: dict[str, Any]) -> list[str]:
    """Business names of the capability/experience labels counted in a decision."""
    return [str(row.get("name") or cid) for cid, row in _counted_criteria(criteria).items()]


def _first_evidence_text(row: dict[str, Any]) -> str:
    evidence = row.get("evidence") or []
    if evidence and isinstance(evidence[0], dict):
        return str(evidence[0].get("text") or "")
    return ""


def decide_candidate(criteria: dict[str, Any], assessment: dict[str, Any], resume_facts: dict[str, Any] | None = None) -> dict[str, Any]:
    criterion_defs = {str(x.get("criterion_id") or ""): x for x in (criteria.get("criteria") or [])}
    needs_verification: list[dict[str, Any]] = []
    interview_focus: list[dict[str, Any]] = []
    preference_signals: list[dict[str, Any]] = []
    veto_items: list[dict[str, Any]] = []
    fail_reasons: list[dict[str, Any]] = []
    # Manager-facing tag hit rate counts only capability/experience labels.
    # Eligibility gates and traits are reported separately (traits only in
    # interview focus), so they must not inflate the numerator or denominator.
    counted_definitions = _counted_criteria(criteria)
    applicable_tag_count = len(counted_definitions)
    matched_tag_count = 0
    matched_tag_names: list[str] = []

    hard = assessment.get("hard_checks") or {}
    hard_summary: dict[str, Any] = {"pass": True, "details": {}}

    edu = hard.get("education") or {}
    edu_status = str(edu.get("status") or "unknown")
    hard_summary["details"]["education"] = {"status": edu_status, "pass": edu_status != "not_matched"}
    if edu_status == "not_matched":
        hard_summary["pass"] = False
        fail_reasons.append({"criterion": "学历", "evidence": _first_evidence_text(edu), "rule": "education_not_matched"})
    elif edu_status == "unknown":
        needs_verification.append({"item": "学历", "reason": edu.get("reasoning_summary") or "简历未提供足够学历/专业信息"})

    cert = hard.get("certification") or {}
    cert_status = str(cert.get("status") or "unknown")
    accepted_cert_statuses = set((criteria.get("hard_filters") or {}).get(
        "certification_accepted_statuses", ["valid", "pending_exam"]))
    cert_fail = cert_status in {"expired", "none"} or (
        cert_status == "eligible_to_apply" and cert_status not in accepted_cert_statuses)
    hard_summary["details"]["certification"] = {"status": cert_status, "pass": not cert_fail}
    if cert_fail:
        hard_summary["pass"] = False
        fail_reasons.append({"criterion": "检验资格证", "evidence": _first_evidence_text(cert), "rule": f"certification_{cert_status}"})
    elif cert_status == "unknown":
        needs_verification.append({"item": "检验资格证", "reason": cert.get("reasoning_summary") or "简历未提供足够证书信息"})
    elif cert_status in {"valid", "pending_exam", "eligible_to_apply"}:
        if cert_status == "eligible_to_apply":
            needs_verification.append({"item": "报考资格", "reason": "应届报考资格需核实；尚未报名、未持证，不代表已取得检验资格证"})

    loc = hard.get("location") or {}
    loc_status = str(loc.get("status") or "unknown")
    loc_fail = loc_status == "refused"
    hard_summary["details"]["location"] = {"status": loc_status, "pass": not loc_fail}
    if loc_fail:
        hard_summary["pass"] = False
        fail_reasons.append({"criterion": "工作地点", "evidence": _first_evidence_text(loc), "rule": "location_refused"})
    elif loc_status == "unknown":
        needs_verification.append({"item": "工作地点", "reason": loc.get("reasoning_summary") or "简历未说明是否接受目标工作地点"})

    red_hits = []
    for red in assessment.get("red_line_assessments") or []:
        status = str(red.get("status") or "unknown")
        rid = str(red.get("red_line_id") or "")
        if status == "hit":
            hard_summary["pass"] = False
            red_hits.append(rid)
            fail_reasons.append({"criterion": rid or "红线", "evidence": _first_evidence_text(red), "rule": "red_line_hit"})
        elif status == "unknown":
            needs_verification.append({"item": rid or "红线", "reason": red.get("reasoning_summary") or "红线条件无法从简历确认"})
    hard_summary["details"]["red_lines"] = {"pass": not red_hits, "hits": red_hits}

    confirmed_resume_evidence_count = 0
    for row in assessment.get("criteria_assessments") or []:
        cid = str(row.get("criterion_id") or "")
        definition = criterion_defs.get(cid) or {}
        name = str(definition.get("name") or cid)
        mode = str(definition.get("decision_mode") or "")
        level = str(definition.get("level") or "")
        result = str(row.get("criterion_result") or "unknown")
        evidence_status = str(row.get("evidence_status") or "none")
        evidence_text = _first_evidence_text(row)
        reason = str(row.get("reasoning_summary") or "")

        if (str(definition.get("dimension") or "") in _COUNTED_DIMENSIONS
                and result == "satisfied" and evidence_status == "confirmed"):
            matched_tag_count += 1
            matched_tag_names.append(name)

        if mode == "hard_gate":
            if result == "not_satisfied":
                veto_items.append({
                    "criterion": name,
                    "criterion_id": cid,
                    "decision_mode": mode,
                    "criterion_result": result,
                    "evidence_status": evidence_status,
                    "detail": reason,
                    "evidence": evidence_text,
                })
            elif result == "unknown":
                needs_verification.append({"item": name, "reason": reason or "该必须条件无法从简历确认"})
        elif mode == "resume_evidence":
            # 必须项若被简历"明确且有据"地证伪，就是 grounded 的资格不符，直接否决；
            # 证据不完整（partial/none）或无法判断（unknown）仍只进待核实，绝不误杀。
            if result == "not_satisfied" and evidence_status == "confirmed" and level == "必须":
                veto_items.append({
                    "criterion": name,
                    "criterion_id": cid,
                    "decision_mode": mode,
                    "criterion_result": result,
                    "evidence_status": evidence_status,
                    "detail": reason,
                    "evidence": evidence_text,
                    "rule": "resume_evidence_confirmed_not_satisfied",
                })
            elif result in {"not_satisfied", "unknown"}:
                needs_verification.append({"item": name, "reason": reason or "该项需人工/电话确认"})
            elif result == "satisfied" and evidence_status == "confirmed":
                confirmed_resume_evidence_count += 1
        elif mode == "interview_verify":
            if result in {"not_satisfied", "unknown"}:
                interview_focus.append({"item": name, "reason": reason or "该项需在面试中确认"})
        elif mode == "preference":
            if result == "satisfied":
                preference_signals.append({
                    "item": name,
                    "criterion_id": cid,
                    "evidence": evidence_text,
                    "evidence_status": evidence_status,
                    "reason": reason,
                })

    decision = "fail" if fail_reasons or veto_items else "pass"
    return {
        "schema_version": "5.0",
        "decision_status": "DECISION_COMPLETE",
        "candidate_id": str(assessment.get("candidate_id") or (resume_facts or {}).get("candidate_id") or ""),
        "name": (resume_facts or {}).get("name"),
        "policy_id": str(criteria.get("policy_id") or ""),
        "request_id": str(criteria.get("request_id") or ""),
        "decision": decision,
        "hard_check_summary": hard_summary,
        "fail_reasons": fail_reasons,
        "veto_items": veto_items,
        "needs_verification": needs_verification,
        "interview_focus": interview_focus,
        "preference_signals": preference_signals,
        "confirmed_resume_evidence_count": confirmed_resume_evidence_count,
        "matched_tag_count": matched_tag_count,
        "matched_tag_names": matched_tag_names,
        "applicable_tag_count": applicable_tag_count,
    }


def recommendation_order_key(decision: dict[str, Any], pool_index: int) -> tuple[Any, ...]:
    """内部推荐顺序（不是人才分数，也不对外暴露）。

    排序主轴 = 命中标签数（受理的业务要求/经历/能力条数）多者优先；
    同命中数时，先用"没有待核实项"的，再看加分项与已确认事实数，
    最后按候选池原始顺序保证稳定。
    """
    is_fail = str(decision.get("decision") or "") != "pass"
    needs = decision.get("needs_verification") or []
    matched = int(decision.get("matched_tag_count") or 0)
    pref_count = len(decision.get("preference_signals") or [])
    evidence_count = int(decision.get("confirmed_resume_evidence_count") or 0)
    return (is_fail, -matched, 1 if needs else 0, -pref_count, -evidence_count, len(needs), int(pool_index))
