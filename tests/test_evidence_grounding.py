from rn.resume_screening.decision.evidence_grounding import validate_grounding


def resume_facts():
    return {
        "candidate_id": "C1",
        "name": "A",
        "raw_text": "负责 Keepalived 双机部署，通过 VIP 自动漂移实现故障节点切换，将中断降低到2分钟。",
        "work_experiences": [{
            "company": "甲",
            "position": "运维工程师",
            "description": "负责 Keepalived 双机部署，通过 VIP 自动漂移实现故障节点切换。",
        }],
        "certifications": ["PMP"],
    }


def evidence(quote):
    return {
        "schema_version": "3.0",
        "candidate_id": "C1",
        "job_id": "J1",
        "evaluation_status": "COMPLETE",
        "tag_assessments": [{
            "tag_id": "T01",
            "evidence_level": "CASE",
            "confidence": 0.95,
            "evidence": [{"text": quote, "source_path": "raw_text"}],
            "reasoning_summary": "有具体实施行为",
        }],
        "requirement_assessments": [],
    }


def test_exact_resume_quote_is_grounded():
    result = validate_grounding(resume_facts(), evidence("通过 VIP 自动漂移实现故障节点切换"))
    assert result["status"] == "GROUNDED"
    assert result["errors"] == []


def test_hallucinated_quote_blocks_scoring():
    result = validate_grounding(resume_facts(), evidence("将核心系统SLA提升到99.999%"))
    assert result["status"] == "GROUNDING_FAILED"
    assert any("UNGROUNDED" in e for e in result["errors"])


def test_normalized_whitespace_does_not_break_real_quote():
    facts = resume_facts()
    facts["raw_text"] = "负责 A/B   测试\n并完成  复盘"
    ev = evidence("A/B 测试 并完成 复盘")
    result = validate_grounding(facts, ev)
    assert result["status"] == "GROUNDED"


def test_legacy_generic_source_label_maps_to_authoritative_work_experience():
    ev = evidence("通过 VIP 自动漂移实现故障节点切换")
    ev["tag_assessments"][0]["evidence"][0].pop("source_path")
    ev["tag_assessments"][0]["evidence"][0]["source"] = "work_experience"
    result = validate_grounding(resume_facts(), ev)
    assert result["status"] == "GROUNDED"
