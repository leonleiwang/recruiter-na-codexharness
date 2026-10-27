import json

import pytest

from rn.resume_screening.decision.decision_evidence_evaluator import DecisionEvidenceEvaluator
from rn.resume_screening.decision.evidence_grounding import validate_decision_grounding
from rn.resume_screening.decision.semantic_evidence_evaluator import EvidenceEvaluationError


CRITERIA = {
    "schema_version": "5.0",
    "policy_id": "inspection-technician-v1",
    "request_id": "REQ-1",
    "hard_filters": {
        "education": "大专及以上，医学检验相关专业",
        "certification": "检验士/检验师资格证",
        "location": "杭州",
        "red_lines": ["纯科研实验室经验，无临床检验实操经历"],
    },
    "criteria": [
        {"criterion_id": "C01", "name": "检验科经验", "decision_mode": "resume_evidence", "description": "≥1年检验科经验", "evidence_hints": "检验科/ICL"},
        {"criterion_id": "C02", "name": "人员带教经验", "decision_mode": "preference", "description": "带教新人", "evidence_hints": "带教/培训"},
    ],
}

FACTS = {
    "candidate_id": "C1",
    "name": "张三",
    "degree": "本科",
    "education": {"level": "本科", "major": "医学检验技术"},
    "location": "上海",
    "certifications": ["临床医学检验技术师"],
    "work_experiences": [{"description": "2022-2025 杭州某医院检验科，负责生化检测，并带教2名新人"}],
    "raw_text": "本科 医学检验技术。持临床医学检验技术师证。2022-2025 杭州某医院检验科，负责生化检测，并带教2名新人。愿意到杭州工作。",
}


def _valid_doc():
    return {
        "schema_version": "5.0",
        "candidate_id": "C1",
        "policy_id": "inspection-technician-v1",
        "evaluation_status": "COMPLETE",
        "hard_checks": {
            "education": {"status": "matched", "evidence": [{"text": "本科 医学检验技术", "source_path": "raw_text"}], "reasoning_summary": "符合"},
            "certification": {"status": "valid", "evidence": [{"text": "持临床医学检验技术师证", "source_path": "raw_text"}], "reasoning_summary": "有效"},
            "location": {"status": "willing", "evidence": [{"text": "愿意到杭州工作", "source_path": "raw_text"}], "reasoning_summary": "愿意迁移"},
        },
        "red_line_assessments": [
            {"red_line_id": "RL01", "status": "clear", "evidence": [], "reasoning_summary": "有临床检验经历"}
        ],
        "criteria_assessments": [
            {"criterion_id": "C01", "criterion_result": "satisfied", "evidence_status": "confirmed", "evidence": [{"text": "2022-2025 杭州某医院检验科", "source_path": "raw_text"}], "reasoning_summary": "超过1年"},
            {"criterion_id": "C02", "criterion_result": "satisfied", "evidence_status": "confirmed", "evidence": [{"text": "带教2名新人", "source_path": "raw_text"}], "reasoning_summary": "有带教"},
        ],
    }


def _evaluator(doc):
    def post_json(url, headers, payload, timeout):
        return {"output_text": json.dumps(doc, ensure_ascii=False)}
    return DecisionEvidenceEvaluator(
        base_url="https://example.test/v1",
        model="fake",
        api_key="secret",
        post_json=post_json,
    )


def test_evaluator_accepts_strict_complete_contract_and_unknown_without_evidence():
    doc = _valid_doc()
    doc["hard_checks"]["location"] = {"status": "unknown", "evidence": [], "reasoning_summary": "未说明意愿"}
    result = _evaluator(doc).evaluate(CRITERIA, FACTS)
    assert result["hard_checks"]["location"]["status"] == "unknown"
    assert result["criteria_assessments"][0]["criterion_result"] == "satisfied"
    assert "score" not in json.dumps(result, ensure_ascii=False).lower()


def test_evaluator_rejects_invalid_criterion_result_enum():
    doc = _valid_doc()
    doc["criteria_assessments"][0]["criterion_result"] = "fail"
    with pytest.raises(EvidenceEvaluationError) as exc:
        _evaluator(doc).evaluate(CRITERIA, FACTS)
    assert exc.value.code == "LLM_CONTRACT_INVALID"


def test_evaluator_downgrades_definitive_result_without_evidence():
    """没有逐字证据的"确定性结论"不可信：降级为 unknown/none 并留痕，
    而不是让整份候选人评估抛错消失（ISSUE-018）。"""
    doc = _valid_doc()
    doc["criteria_assessments"][0].update({"criterion_result": "not_satisfied", "evidence_status": "confirmed", "evidence": []})

    repaired = _evaluator(doc).evaluate(CRITERIA, FACTS)

    row = repaired["criteria_assessments"][0]
    assert row["criterion_result"] == "unknown"
    assert row["evidence_status"] == "none"
    assert any(r["repair"] == "downgraded_to_unknown" for r in repaired["contract_repairs"])


def test_evaluator_keeps_grounded_definitive_result_untouched():
    doc = _valid_doc()
    doc["criteria_assessments"][0].update({
        "criterion_result": "not_satisfied", "evidence_status": "confirmed",
        "evidence": [{"text": "原文引用", "source_path": "raw_text"}],
    })

    kept = _evaluator(doc).evaluate(CRITERIA, FACTS)

    assert kept["criteria_assessments"][0]["criterion_result"] == "not_satisfied"
    assert "contract_repairs" not in kept


def test_evaluator_requires_exactly_one_assessment_per_criterion():
    doc = _valid_doc()
    doc["criteria_assessments"] = doc["criteria_assessments"][:1]
    with pytest.raises(EvidenceEvaluationError) as exc:
        _evaluator(doc).evaluate(CRITERIA, FACTS)
    assert exc.value.code == "LLM_CONTRACT_INVALID"


def test_decision_grounding_accepts_real_quotes_and_rejects_fabricated_negative_fact():
    good = _valid_doc()
    assert validate_decision_grounding(FACTS, good)["status"] == "GROUNDED"

    bad = _valid_doc()
    bad["criteria_assessments"][0] = {
        "criterion_id": "C01",
        "criterion_result": "not_satisfied",
        "evidence_status": "confirmed",
        "evidence": [{"text": "仅有6个月检验科经验", "source_path": "raw_text"}],
        "reasoning_summary": "不足1年",
    }
    validation = validate_decision_grounding(FACTS, bad)
    assert validation["status"] == "GROUNDING_FAILED"
    assert any("criterion:C01" in x for x in validation["errors"])


def test_explicit_negative_hard_check_requires_grounded_evidence():
    doc = _valid_doc()
    doc["hard_checks"]["certification"] = {
        "status": "none",
        "evidence": [{"text": "本人没有检验资格证", "source_path": "raw_text"}],
        "reasoning_summary": "明确无证",
    }
    validation = validate_decision_grounding(FACTS, doc)
    assert validation["status"] == "GROUNDING_FAILED"
    assert any("hard_check:certification" in x for x in validation["errors"])
