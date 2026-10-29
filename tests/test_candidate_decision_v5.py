from rn.resume_screening.decision.candidate_decision_v5 import decide_candidate, recommendation_order_key


CRITERIA = {
    "policy_id": "inspection-technician-v1",
    "request_id": "REQ1",
    "criteria": [
        {"criterion_id": "C01", "name": "必须罗氏平台经验", "dimension": "request_extra", "decision_mode": "hard_gate", "level": "必须"},
        {"criterion_id": "C02", "name": "检验科经验", "dimension": "experience", "decision_mode": "resume_evidence", "level": "必须"},
        {"criterion_id": "C03", "name": "沟通表达能力", "dimension": "capability", "decision_mode": "interview_verify", "level": "必须"},
        {"criterion_id": "C04", "name": "人员带教经验", "dimension": "experience", "decision_mode": "preference", "level": "优先"},
    ],
}


def assessment(*, education="matched", certification="valid", location="match", red="clear", c1="satisfied", c2="satisfied", c2_status=None, c3="unknown", c4="unknown"):
    def row(cid, result, evidence_status=None):
        if evidence_status is None:
            evidence_status = "confirmed" if result in {"satisfied", "not_satisfied"} else "none"
        evidence = [] if result == "unknown" else [{"text": f"{cid}-evidence", "source_path": "raw_text"}]
        return {
            "criterion_id": cid,
            "criterion_result": result,
            "evidence_status": evidence_status,
            "evidence": evidence,
            "reasoning_summary": f"{cid}-{result}",
        }
    return {
        "candidate_id": "CAND-1",
        "policy_id": "inspection-technician-v1",
        "hard_checks": {
            "education": {"status": education, "evidence": [], "reasoning_summary": education},
            "certification": {"status": certification, "evidence": [], "reasoning_summary": certification},
            "location": {"status": location, "evidence": [], "reasoning_summary": location},
        },
        "red_line_assessments": [{"red_line_id": "RL01", "status": red, "evidence": [], "reasoning_summary": red}],
        "criteria_assessments": [row("C01", c1), row("C02", c2, c2_status), row("C03", c3), row("C04", c4)],
    }


def test_unknown_red_line_uses_approved_business_label_without_changing_decision():
    criteria = {**CRITERIA, "hard_filters": {"red_lines": ["资格证书已过期、暂停或吊销"]}}
    result = decide_candidate(criteria, assessment(red="unknown"))
    assert result["decision"] == "pass"
    assert result["needs_verification"] == [
        {"item": "资格证书已过期、暂停或吊销", "reason": "unknown"}]
    assert result["hard_check_summary"]["details"]["red_lines"] == {"pass": True, "hits": []}


def test_unknown_unmapped_red_line_does_not_expose_internal_id():
    result = decide_candidate(CRITERIA, assessment(red="unknown"))
    assert result["decision"] == "pass"
    assert result["needs_verification"][0]["item"] == "资格限制条件"


def test_unknown_hard_checks_do_not_fail_candidate():
    result = decide_candidate(CRITERIA, assessment(education="unknown", certification="unknown", location="unknown"))
    assert result["decision"] == "pass"
    items = {x["item"] for x in result["needs_verification"]}
    assert {"学历", "检验资格证", "工作地点"}.issubset(items)


def test_explicit_negative_hard_check_fails_candidate():
    result = decide_candidate(CRITERIA, assessment(certification="expired"))
    assert result["decision"] == "fail"
    assert any(x["rule"] == "certification_expired" for x in result["fail_reasons"])


def test_hard_gate_not_satisfied_vetoes_but_unknown_only_verifies():
    failed = decide_candidate(CRITERIA, assessment(c1="not_satisfied"))
    assert failed["decision"] == "fail"
    assert failed["veto_items"][0]["criterion"] == "必须罗氏平台经验"

    unknown = decide_candidate(CRITERIA, assessment(c1="unknown"))
    assert unknown["decision"] == "pass"
    assert any(x["item"] == "必须罗氏平台经验" for x in unknown["needs_verification"])


def test_resume_evidence_confirmed_not_satisfied_vetoes_a_required_criterion():
    """业务标注要求：必须项若被简历明确证伪，必须否决（v5 曾漏判此类候选人）。"""
    result = decide_candidate(CRITERIA, assessment(c2="not_satisfied", c2_status="confirmed"))

    assert result["decision"] == "fail"
    assert any(v["rule"] == "resume_evidence_confirmed_not_satisfied" for v in result["veto_items"])


def test_resume_evidence_not_satisfied_without_confirmed_evidence_only_verifies():
    """证据不完整（partial）时不得否决，只能进待核实，避免误杀。"""
    result = decide_candidate(CRITERIA, assessment(c2="not_satisfied", c2_status="partial"))

    assert result["decision"] == "pass"
    assert not result["veto_items"]
    assert any(x["item"] == "检验科经验" for x in result["needs_verification"])


def test_resume_evidence_unknown_still_only_verifies():
    result = decide_candidate(CRITERIA, assessment(c2="unknown"))

    assert result["decision"] == "pass"
    assert not result["veto_items"]
    assert any(x["item"] == "检验科经验" for x in result["needs_verification"])


def test_interview_verify_unknown_or_not_satisfied_becomes_interview_focus():
    unknown = decide_candidate(CRITERIA, assessment(c3="unknown"))
    assert any(x["item"] == "沟通表达能力" for x in unknown["interview_focus"])
    not_satisfied = decide_candidate(CRITERIA, assessment(c3="not_satisfied"))
    assert any(x["item"] == "沟通表达能力" for x in not_satisfied["interview_focus"])
    assert not_satisfied["decision"] == "pass"


def test_preference_signal_only_when_satisfied_and_keeps_evidence():
    result = decide_candidate(CRITERIA, assessment(c4="satisfied"))
    assert result["decision"] == "pass"
    assert [x["item"] for x in result["preference_signals"]] == ["人员带教经验"]
    assert result["preference_signals"][0]["evidence"] == "C04-evidence"


def test_recommendation_order_is_grouped_without_exposed_score():
    ready_two_prefs = {"decision": "pass", "needs_verification": [], "preference_signals": [{}, {}], "confirmed_resume_evidence_count": 2}
    ready_one_pref = {"decision": "pass", "needs_verification": [], "preference_signals": [{}], "confirmed_resume_evidence_count": 3}
    verify_many_prefs = {"decision": "pass", "needs_verification": [{"item": "x"}], "preference_signals": [{}, {}, {}], "confirmed_resume_evidence_count": 4}
    failed = {"decision": "fail", "needs_verification": [], "preference_signals": [], "confirmed_resume_evidence_count": 9}

    ordered = sorted(
        [("A", ready_one_pref, 2), ("B", verify_many_prefs, 1), ("C", ready_two_prefs, 3), ("D", failed, 0)],
        key=lambda x: recommendation_order_key(x[1], x[2]),
    )
    assert [x[0] for x in ordered] == ["C", "A", "B", "D"]
    assert all("score" not in key for key in ready_two_prefs)


def test_recommendation_order_prefers_more_matched_tags():
    """排序主轴是命中标签数：命中多的排前面，且仍不引入数值分。"""
    few_tags = {"decision": "pass", "needs_verification": [], "preference_signals": [{}, {}],
                "confirmed_resume_evidence_count": 9, "matched_tag_count": 4}
    many_tags = {"decision": "pass", "needs_verification": [{"item": "工作地点"}], "preference_signals": [],
                 "confirmed_resume_evidence_count": 1, "matched_tag_count": 13}

    ordered = sorted([("few", few_tags, 0), ("many", many_tags, 1)],
                     key=lambda x: recommendation_order_key(x[1], x[2]))

    assert [x[0] for x in ordered] == ["many", "few"]


def test_tag_counts_cover_only_capability_and_experience():
    """「符合标签 x/y」只数能力/经验：硬性资格与特质不得进分子分母。"""
    criteria = {
        **CRITERIA,
        "criteria": [
            *CRITERIA["criteria"],
            {"criterion_id": "C05", "name": "标签五", "dimension": "experience", "decision_mode": "resume_evidence"},
            {"criterion_id": "C06", "name": "标签六", "dimension": "capability", "decision_mode": "resume_evidence"},
            {"criterion_id": "C07", "name": "标签七", "dimension": "trait", "decision_mode": "interview_verify"},
            {"criterion_id": "C08", "name": "标签八", "dimension": "request_extra", "decision_mode": "hard_gate"},
        ],
    }
    doc = assessment(c3="satisfied", c4="satisfied")
    doc["criteria_assessments"].extend([
        {"criterion_id": "C05", "criterion_result": "satisfied", "evidence_status": "confirmed", "evidence": [{"text": "五", "source_path": "raw_text"}]},
        {"criterion_id": "C06", "criterion_result": "satisfied", "evidence_status": "confirmed", "evidence": [{"text": "六", "source_path": "raw_text"}]},
        {"criterion_id": "C07", "criterion_result": "satisfied", "evidence_status": "confirmed", "evidence": [{"text": "七", "source_path": "raw_text"}]},
        {"criterion_id": "C08", "criterion_result": "satisfied", "evidence_status": "confirmed", "evidence": [{"text": "八", "source_path": "raw_text"}]},
    ])

    result = decide_candidate(criteria, doc)

    # 适用标签 = C02/C03/C04/C05/C06（能力+经验）；C01/C08 是额外硬要求，C07 是特质。
    assert result["applicable_tag_count"] == 5
    assert result["matched_tag_count"] == 5
    assert result["matched_tag_names"] == ["检验科经验", "沟通表达能力", "人员带教经验", "标签五", "标签六"]
    assert "学历" not in result["matched_tag_names"]
    assert "检验资格证" not in result["matched_tag_names"]
    assert "工作地点" not in result["matched_tag_names"]
    assert result["decision"] == "pass"
