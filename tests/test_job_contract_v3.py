from rn.resume_screening.decision.job_contract_v3 import build_job_contract, normalize_resume_facts


def legacy_job():
    return {
        "id": "J-001",
        "title": "平台工程师",
        "location": "杭州",
        "description": "保障平台稳定性",
        "hard_requirements": [
            {"name": "地点", "value": "杭州", "verify_type": "hard"},
            {"name": "工龄", "value": "3年", "verify_type": "soft"},
        ],
        "portrait_tags": [
            {"category": "capability", "tags": [
                {"name": "高可用架构", "weight": 0.6},
                {"name": "故障治理", "weight": 0.4},
            ]}
        ],
    }


def test_build_job_contract_preserves_semantic_tags_and_normalizes_weights():
    contract = build_job_contract(legacy_job())
    assert contract["schema_version"] == "3.0"
    assert contract["job_id"] == "J-001"
    assert [t["name"] for t in contract["tags"]] == ["高可用架构", "故障治理"]
    assert abs(sum(t["weight"] for t in contract["tags"]) - 1.0) < 1e-9
    assert all(t["definition"] for t in contract["tags"])


def test_location_hard_gate_policy_never_treats_current_city_mismatch_as_fail_by_itself():
    contract = build_job_contract(legacy_job())
    req = contract["requirements"][0]
    assert req["gate_type"] == "HARD_GATE"
    assert req["requirement_type"] == "location"
    assert "UNKNOWN" in req["decision_policy"]
    assert "明确" in req["decision_policy"]


def test_normalize_resume_facts_maps_resume_id_and_degree_without_semantic_scoring():
    facts = normalize_resume_facts({
        "resume_id": "R-1",
        "name": "候选人",
        "education": {"level": "硕士"},
        "location": "上海",
        "raw_text": "候选人，硕士，现居上海。",
        "work_experiences": [],
    })
    assert facts["candidate_id"] == "R-1"
    assert facts["degree"] == "硕士"
    assert facts["location"] == "上海"
    assert facts["raw_text"].startswith("候选人")
