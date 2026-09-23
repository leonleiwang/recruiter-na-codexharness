"""_recommendation_reasons 渲染规则：特质项（trait 维度）一律不进推荐依据。"""
from rn.screening_v5 import _recommendation_reasons


CRITERIA = {
    "criteria": [
        {"criterion_id": "C01", "name": "规范操作与执行能力", "dimension": "capability", "decision_mode": "resume_evidence"},
        {"criterion_id": "C06", "name": "检验科经验", "dimension": "experience", "decision_mode": "resume_evidence"},
        {"criterion_id": "C12", "name": "积极主动", "dimension": "trait", "decision_mode": "preference"},
        {"criterion_id": "C11", "name": "责任心", "dimension": "trait", "decision_mode": "interview_verify"},
    ],
}


def _row(cid: str, result: str, status: str = "confirmed") -> dict:
    return {"criterion_id": cid, "criterion_result": result, "evidence_status": status,
            "evidence": [{"text": f"{cid}-evidence", "source_path": "raw_text"}]}


def test_trait_preference_signal_is_omitted_from_recommendation_reasons():
    decision = {
        "preference_signals": [
            {"criterion_id": "C12", "item": "积极主动", "evidence": "全程参与文件编写", "evidence_status": "confirmed"},
            {"criterion_id": "C07", "item": "人员带教经验", "evidence": "带教2人", "evidence_status": "confirmed"},
        ]
    }
    assessment = {"criteria_assessments": [_row("C01", "satisfied"), _row("C06", "satisfied")]}

    reasons = _recommendation_reasons(CRITERIA, assessment, decision)

    assert "积极主动" not in " ".join(reasons)
    assert "人员带教经验" in reasons[0]


def test_trait_assessment_row_is_omitted_even_if_satisfied():
    decision = {"preference_signals": []}
    assessment = {"criteria_assessments": [_row("C12", "satisfied"), _row("C11", "satisfied"), _row("C06", "satisfied")]}

    reasons = _recommendation_reasons(CRITERIA, assessment, decision)

    assert "积极主动" not in reasons and "责任心" not in reasons
    assert reasons == ["检验科经验：C06-evidence"]


def test_interview_verify_capability_is_also_excluded():
    decision = {"preference_signals": []}
    # decision_mode=interview_verify 即便是 capability 维度，也不作为推荐依据。
    criteria_with_iv = {**CRITERIA, "criteria": [
        {**CRITERIA["criteria"][0], "decision_mode": "interview_verify"},
        *CRITERIA["criteria"][1:],
    ]}
    assessment = {"criteria_assessments": [
        {"criterion_id": "C01", "criterion_result": "satisfied", "evidence_status": "confirmed",
         "evidence": [{"text": "evidence", "source_path": "raw_text"}]},
    ]}

    reasons = _recommendation_reasons(criteria_with_iv, assessment, decision)

    assert reasons == []


def test_only_resume_evidence_satisfied_confirmed_items_appear():
    decision = {"preference_signals": []}
    assessment = {"criteria_assessments": [
        _row("C01", "satisfied", "confirmed"),
        _row("C01", "satisfied", "partial"),
        _row("C06", "not_satisfied", "confirmed"),
        _row("C06", "unknown", "none"),
    ]}

    reasons = _recommendation_reasons(CRITERIA, assessment, decision)

    assert reasons == ["规范操作与执行能力：C01-evidence"]


def test_returns_at_most_four_reasons():
    decision = {"preference_signals": []}
    assessment = {"criteria_assessments": [_row(f"C{i:02d}", "satisfied", "confirmed") for i in range(1, 7)]}

    reasons = _recommendation_reasons(CRITERIA, assessment, decision)

    assert len(reasons) <= 4
