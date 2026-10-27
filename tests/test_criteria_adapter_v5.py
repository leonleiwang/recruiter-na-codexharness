from types import SimpleNamespace

import pytest

from rn.resume_screening.decision.criteria_adapter_v5 import (
    build_actual_criteria,
    load_inspection_technician_policy,
    supports_inspection_technician,
)


def _request(title="检验技术员", profile_id="PROFILE-412167F2937C", **fields):
    base = {
        "position_title": title,
        "location": "上海",
        "headcount": 2,
        "salary_range": "10-15K",
        "extra_hard_requirements": [],
    }
    base.update(fields)
    return {
        "request_id": "REQ-1",
        "position_title": title,
        "request": {
            "fields": base,
            "profile_match": {"status": "MATCHED", "profile_id": profile_id},
        },
    }


def test_policy_is_v23_inspection_technician_contract():
    policy = load_inspection_technician_policy()
    assert policy["policy_id"] == "inspection-technician-v1"
    assert policy["schema_version"] == "5.0"
    assert set(policy["supported_profile_ids"]) == {
        "PROFILE-91603AA55F6E",
        "PROFILE-412167F2937C",
    }
    assert any(x["name"] == "检验科经验" and x["decision_mode"] == "resume_evidence" for x in policy["experiences"])
    assert all("score" not in str(k).lower() for k in policy.keys())


def test_scope_supports_only_generic_freshgrad_and_social_inspection_technician():
    assert supports_inspection_technician(_request()) is True
    assert supports_inspection_technician(_request(profile_id="PROFILE-91603AA55F6E")) is True
    assert supports_inspection_technician(_request(title="检验技术员", profile_id="")) is True
    assert supports_inspection_technician(_request(title="微生物检验技术员", profile_id="PROFILE-0C5EA85E3C37")) is False
    assert supports_inspection_technician(_request(title="二代测序检验技术员（全流程岗）", profile_id="PROFILE-4418074B1BD0")) is False
    assert supports_inspection_technician(_request(title="医学联络官", profile_id="PROFILE-2FC5FDEBF75E")) is False


def test_actual_criteria_uses_request_location_and_keeps_internal_decision_modes_hidden_in_contract():
    request = _request(location="南京")
    profile = SimpleNamespace(profile_id="PROFILE-412167F2937C", name="检验技术员（社招）")
    criteria = build_actual_criteria(request, profile)
    assert criteria["policy_id"] == "inspection-technician-v1"
    assert criteria["position"]["location"] == "南京"
    assert criteria["position"]["headcount"] == 2
    assert criteria["hard_filters"]["location"] == "南京"
    modes = {row["decision_mode"] for row in criteria["criteria"]}
    assert modes == {"resume_evidence", "interview_verify", "preference"}
    assert criteria["product_visibility"]["decision_mode"] == "internal_only"


def test_experience_bar_follows_the_matched_profile_not_the_shared_policy():
    """应届与社招共用一条 policy，但经历口径不同：
    应届=实习即可/可培养（优先，不否决）；社招=≥1年（必须，明确不足即否决）。"""
    freshgrad = build_actual_criteria(
        _request(profile_id="PROFILE-91603AA55F6E"),
        SimpleNamespace(profile_id="PROFILE-91603AA55F6E", name="检验技术员（应届）"),
    )
    social = build_actual_criteria(
        _request(profile_id="PROFILE-412167F2937C"),
        SimpleNamespace(profile_id="PROFILE-412167F2937C", name="检验技术员（社招）"),
    )

    fresh = next(r for r in freshgrad["criteria"] if r["name"] == "检验科经验")
    experienced = next(r for r in social["criteria"] if r["name"] == "检验科经验")

    assert (fresh["level"], fresh["decision_mode"]) == ("优先", "preference")
    assert (experienced["level"], experienced["decision_mode"]) == ("必须", "resume_evidence")
    assert "实习" in fresh["description"] and "≥1年" in experienced["description"]
    assert fresh["source"] == "profile_override:PROFILE-91603AA55F6E"
    assert [o["criterion_id"] for o in freshgrad["profile_criterion_overrides_applied"]] == ["C06"]
    # 社招画像原文是"3年以上检验试剂管理经验"，该口径同样按画像覆盖。
    assert [o["criterion_id"] for o in social["profile_criterion_overrides_applied"]] == ["C06", "C07"]


def test_each_profile_is_evaluated_only_against_its_own_profile_items():
    """policy 是应届与社招两份画像的并集；不属于某画像的判据在该画像下必须整条剔除，
    否则会拿另一画像的能力去要求候选人，命中数分母也会跟着错。"""
    freshgrad = build_actual_criteria(
        _request(profile_id="PROFILE-91603AA55F6E"),
        SimpleNamespace(profile_id="PROFILE-91603AA55F6E", name="检验技术员（应届）"),
    )
    social = build_actual_criteria(
        _request(profile_id="PROFILE-412167F2937C"),
        SimpleNamespace(profile_id="PROFILE-412167F2937C", name="检验技术员（社招）"),
    )

    fresh_names = {r["name"] for r in freshgrad["criteria"]}
    social_names = {r["name"] for r in social["criteria"]}

    assert "专业理论知识" in fresh_names and "专业理论知识" not in social_names
    assert "持续学习" in fresh_names and "持续学习" not in social_names
    assert "试剂耗材管理能力" in social_names and "试剂耗材管理能力" not in fresh_names
    assert "问题分析与解决能力" in social_names and "问题分析与解决能力" not in fresh_names
    assert "责任心" in social_names and "责任心" not in fresh_names

    assert [x["criterion_id"] for x in freshgrad["profile_criterion_exclusions"]] == ["C02", "C13", "C07", "C08", "C11"]
    assert [x["criterion_id"] for x in social["profile_criterion_exclusions"]] == ["C14", "C15", "C16"]
    # 应届 5 能力 + 2 经历 + 4 特质 = 11 条判据（标签总数 14）；社招 6 + 3 + 4 = 13 条（标签总数 16）
    assert len(freshgrad["criteria"]) == 11
    assert len(social["criteria"]) == 13


def test_unknown_profile_still_gets_every_policy_criterion():
    criteria = build_actual_criteria(_request(profile_id=""), None)
    assert criteria["profile_criterion_exclusions"] == []
    assert len(criteria["criteria"]) == 16


def test_unknown_profile_keeps_the_shared_policy_criteria():
    criteria = build_actual_criteria(_request(profile_id=""), None)

    assert criteria["profile_criterion_overrides_applied"] == []
    row = next(r for r in criteria["criteria"] if r["name"] == "检验科经验")
    assert (row["level"], row["decision_mode"]) == ("必须", "resume_evidence")


def test_explicit_extra_must_haves_become_hard_gate_criteria():
    request = _request(extra_hard_requirements=["必须有罗氏免疫平台实操经验", "必须接受夜班"])
    criteria = build_actual_criteria(request)
    extras = [x for x in criteria["criteria"] if x["dimension"] == "request_extra"]
    assert [x["name"] for x in extras] == ["必须有罗氏免疫平台实操经验", "必须接受夜班"]
    assert all(x["level"] == "必须" for x in extras)
    assert all(x["decision_mode"] == "hard_gate" for x in extras)


def test_no_or_empty_extra_requirement_does_not_create_fake_gate():
    request = _request(extra_hard_requirements="没有")
    criteria = build_actual_criteria(request)
    assert not [x for x in criteria["criteria"] if x["dimension"] == "request_extra"]


def test_policy_declares_broad_retrieval_queries_for_mineru_and_alibaba_resumes():
    policy = load_inspection_technician_policy()
    queries = policy.get("retrieval_queries") or []
    assert "检验技术员" in queries
    assert "医学检验" in queries
    assert "检验科" in queries
    assert "检验" in queries
