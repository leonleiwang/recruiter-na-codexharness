import copy

from rn.resume_screening.decision.evidence_grounding import repair_decision_grounding, validate_decision_grounding


FACTS = {
    "candidate_id": "C1",
    "degree": "本科",
    "education": {"level": "本科", "major": "医学检验技术"},
    "location": "上海",
    "certifications": ["临床医学检验技术师"],
    "work_experiences": [
        {"description": "2022-2025 杭州某医院检验科，负责生化检测，并带教2名新人"}
    ],
    "raw_text": "本科 医学检验技术。持临床医学检验技术师证。2022-2025 杭州某医院检验科，负责生化检测，并带教2名新人。愿意到杭州工作。",
}


def evidence_doc():
    return {
        "schema_version": "5.0",
        "candidate_id": "C1",
        "policy_id": "inspection-technician-v1",
        "evaluation_status": "COMPLETE",
        "hard_checks": {
            "education": {
                "status": "matched",
                "evidence": [{"text": "本科 医学检验技术", "source_path": "raw_text"}],
                "reasoning_summary": "符合",
            },
            "certification": {
                "status": "valid",
                "evidence": [{"text": "持临床医学检验技术师证", "source_path": "raw_text"}],
                "reasoning_summary": "有效",
            },
            "location": {
                "status": "willing",
                "evidence": [{"text": "愿意到杭州工作", "source_path": "raw_text"}],
                "reasoning_summary": "愿意迁移",
            },
        },
        "red_line_assessments": [
            {"red_line_id": "RL01", "status": "clear", "evidence": [], "reasoning_summary": "未命中"}
        ],
        "criteria_assessments": [
            {
                "criterion_id": "C01",
                "criterion_result": "satisfied",
                "evidence_status": "confirmed",
                "evidence": [{"text": "2022-2025 杭州某医院检验科", "source_path": "raw_text"}],
                "reasoning_summary": "超过1年",
            }
        ],
    }


def test_wrong_source_path_is_canonicalized_when_quote_is_exactly_grounded_elsewhere():
    doc = evidence_doc()
    doc["criteria_assessments"][0]["evidence"][0]["source_path"] = "projects[99].description"

    repaired, report = repair_decision_grounding(FACTS, doc)

    row = repaired["criteria_assessments"][0]
    assert row["criterion_result"] == "satisfied"
    assert row["evidence"][0]["source_path"] == "raw_text"
    assert any(x["action"] == "canonicalized_source_path" for x in report["repairs"])
    assert validate_decision_grounding(FACTS, repaired)["status"] == "GROUNDED"


def test_ungrounded_definitive_criterion_is_downgraded_to_unknown_not_candidate_failure():
    doc = evidence_doc()
    doc["criteria_assessments"][0].update(
        {
            "criterion_result": "not_satisfied",
            "evidence_status": "confirmed",
            "evidence": [{"text": "仅有6个月检验科经验", "source_path": "raw_text"}],
            "reasoning_summary": "不足1年",
        }
    )

    repaired, report = repair_decision_grounding(FACTS, doc)

    row = repaired["criteria_assessments"][0]
    assert row["criterion_result"] == "unknown"
    assert row["evidence_status"] == "none"
    assert row["evidence"] == []
    assert any(x["action"] == "downgraded_criterion_to_unknown" for x in report["repairs"])
    assert validate_decision_grounding(FACTS, repaired)["status"] == "GROUNDED"


def test_ungrounded_negative_hard_check_is_downgraded_to_unknown():
    doc = evidence_doc()
    doc["hard_checks"]["certification"] = {
        "status": "none",
        "evidence": [{"text": "本人没有检验资格证", "source_path": "raw_text"}],
        "reasoning_summary": "明确无证",
    }

    repaired, report = repair_decision_grounding(FACTS, doc)

    cert = repaired["hard_checks"]["certification"]
    assert cert["status"] == "unknown"
    assert cert["evidence"] == []
    assert any(x["action"] == "downgraded_hard_check_to_unknown" for x in report["repairs"])
    assert validate_decision_grounding(FACTS, repaired)["status"] == "GROUNDED"


def test_grounded_fields_remain_unchanged_by_repair():
    original = evidence_doc()
    repaired, report = repair_decision_grounding(FACTS, copy.deepcopy(original))
    assert repaired == original
    assert report["repairs"] == []
