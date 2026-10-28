"""语义评估一致性双跑（strict consistency）回归。

实测：同一份简历、同一策略连跑两次，106 名候选里有 7 人的最终决策翻转，
只有 80% 的人命中标签数完全一致。确定性决策规则会把这种采样噪声放大成
误否决或误推荐，所以每个候选人都跑两遍，不一致的判据退回 unknown（待核实）。
"""
from __future__ import annotations

import copy
import json

import pytest

from rn.resume_screening.decision.decision_evidence_evaluator import DecisionEvidenceEvaluator, merge_consistent_documents
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
        {"criterion_id": "C01", "name": "检验科经验", "decision_mode": "resume_evidence",
         "description": "≥1年检验科经验", "evidence_hints": "检验科/ICL"},
        {"criterion_id": "C02", "name": "人员带教经验", "decision_mode": "preference",
         "description": "带教新人", "evidence_hints": "带教/培训"},
    ],
}

FACTS = {
    "candidate_id": "C1",
    "name": "张三",
    "raw_text": "本科 医学检验技术。持临床医学检验技术师证。2022-2025 杭州某医院检验科，负责生化检测，并带教2名新人。愿意到杭州工作。",
}


def _doc() -> dict:
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
            {"criterion_id": "C01", "criterion_result": "satisfied", "evidence_status": "confirmed",
             "evidence": [{"text": "2022-2025 杭州某医院检验科", "source_path": "raw_text"}], "reasoning_summary": "超过1年"},
            {"criterion_id": "C02", "criterion_result": "satisfied", "evidence_status": "confirmed",
             "evidence": [{"text": "带教2名新人", "source_path": "raw_text"}], "reasoning_summary": "有带教"},
        ],
    }


def _row(doc: dict, cid: str) -> dict:
    return next(r for r in doc["criteria_assessments"] if r["criterion_id"] == cid)


def _evaluator(responses, calls=None, consistency="strict"):
    """responses: 依次返回的文档；元素是 Exception 时抛出。"""
    seq = list(responses)

    def post_json(url, headers, payload, timeout):
        if calls is not None:
            calls.append(payload)
        item = seq.pop(0) if len(seq) > 1 else seq[0]
        if isinstance(item, Exception):
            raise item
        return {"output_text": json.dumps(item, ensure_ascii=False)}

    return DecisionEvidenceEvaluator(
        base_url="https://example.test/v1", model="fake", api_key="secret",
        post_json=post_json, consistency=consistency,
    )


def test_identical_runs_keep_every_conclusion():
    merged = merge_consistent_documents(CRITERIA, _doc(), copy.deepcopy(_doc()))
    assert merged["consistency"]["disagreement_count"] == 0
    assert _row(merged, "C01")["criterion_result"] == "satisfied"
    assert _row(merged, "C01")["evidence_status"] == "confirmed"


def test_disagreement_on_a_must_have_never_becomes_a_veto():
    """第一遍说"不满足"、第二遍说"满足"时，既不能否决也不能算命中。"""
    first, second = _doc(), _doc()
    _row(first, "C01").update({"criterion_result": "not_satisfied", "reasoning_summary": "只有7个月实习"})
    row = _row(second, "C01")
    row["criterion_result"] = "satisfied"

    merged = merge_consistent_documents(CRITERIA, first, second)
    out = _row(merged, "C01")

    assert (out["criterion_result"], out["evidence_status"]) == ("unknown", "partial")
    assert out["consistency_conflict"]["other_result"] == "satisfied"
    assert "不一致" in out["reasoning_summary"]
    assert merged["consistency"]["disagreement_count"] == 1
    assert merged["consistency"]["disagreements"][0]["id"] == "C01"


def test_disagreement_on_evidence_strength_drops_the_tag():
    """两遍都说 satisfied，但证据强度不一致：不算 confirmed 命中。"""
    first, second = _doc(), _doc()
    _row(second, "C02")["evidence_status"] = "partial"

    merged = merge_consistent_documents(CRITERIA, first, second)
    out = _row(merged, "C02")

    assert (out["criterion_result"], out["evidence_status"]) == ("satisfied", "partial")


def test_disagreement_on_eligibility_and_red_lines_falls_back_to_unknown():
    first, second = _doc(), _doc()
    first["hard_checks"]["education"]["status"] = "not_matched"
    first["red_line_assessments"][0]["status"] = "hit"
    second["red_line_assessments"][0]["status"] = "clear"

    merged = merge_consistent_documents(CRITERIA, first, second)

    assert merged["hard_checks"]["education"]["status"] == "unknown"
    assert merged["red_line_assessments"][0]["status"] == "unknown"
    scopes = {d["scope"] for d in merged["consistency"]["disagreements"]}
    assert scopes == {"hard_check.education", "red_line"}


def test_evaluate_runs_the_model_twice_in_strict_mode():
    calls: list = []
    first, second = _doc(), _doc()
    _row(first, "C01")["criterion_result"] = "not_satisfied"
    evaluator = _evaluator([first, second], calls=calls)

    out = evaluator.evaluate(CRITERIA, FACTS)

    assert len(calls) == 2
    assert _row(out, "C01")["criterion_result"] == "unknown"
    assert out["consistency"]["samples"] == 2


def test_consistency_off_runs_the_model_once():
    calls: list = []
    evaluator = _evaluator([_doc()], calls=calls, consistency="off")

    out = evaluator.evaluate(CRITERIA, FACTS)

    assert len(calls) == 1
    assert "consistency" not in out


def test_failed_second_pass_falls_back_to_the_first_result():
    calls: list = []
    evaluator = _evaluator([_doc(), EvidenceEvaluationError("LLM_HTTP_RETRYABLE", "boom")], calls=calls)

    out = evaluator.evaluate(CRITERIA, FACTS)

    assert len(calls) == 2
    assert _row(out, "C01")["criterion_result"] == "satisfied"
    assert "consistency" not in out


def test_merge_does_not_mutate_its_inputs():
    first, second = _doc(), _doc()
    snapshot = copy.deepcopy(first)
    merge_consistent_documents(CRITERIA, first, second)
    assert first == snapshot
