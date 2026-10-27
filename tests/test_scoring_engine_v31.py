from rn.resume_screening.scoring.scoring_engine_v3 import score_candidate


def _resume():
    return {
        "candidate_id": "C1", "name": "A", "degree": "本科",
        "resume_created_at": "2026-09-01", "resume_source": "人才库",
        "raw_text": "负责 Keepalived 双机部署，通过 VIP 自动漂移实现故障节点切换。",
        "work_experiences": [{
            "company": "甲", "position": "运维", "start_date": "2022-01", "end_date": "至今",
            "description": "负责 Keepalived 双机部署，通过 VIP 自动漂移实现故障节点切换。",
        }],
    }


def _job():
    return {
        "schema_version": "3.0", "job_id": "J1",
        "tags": [{"tag_id": "T01", "name": "高可用架构", "category": "capability", "weight": 1.0}],
        "requirements": [],
    }


def _evidence(quote):
    return {
        "schema_version": "3.0", "candidate_id": "C1", "job_id": "J1", "evaluation_status": "COMPLETE",
        "tag_assessments": [{
            "tag_id": "T01", "evidence_level": "CASE", "confidence": 0.9,
            "evidence": [{"text": quote, "source_path": "raw_text"}],
            "reasoning_summary": "具体高可用切换行为",
        }],
        "requirement_assessments": [],
    }


def test_direct_engine_rejects_ungrounded_quote_even_if_pipeline_is_bypassed():
    out = score_candidate(_resume(), _job(), _evidence("将SLA提升至99.999%"), as_of="2026-09-04")
    assert out["scoring_status"] == "EVIDENCE_GROUNDING_FAILED"
    assert out["jm"] is None and out["cs"] is None


def test_direct_engine_accepts_grounded_semantic_case_without_tag_keyword():
    out = score_candidate(_resume(), _job(), _evidence("通过 VIP 自动漂移实现故障节点切换"), as_of="2026-09-04")
    assert out["scoring_status"] == "SCORING_COMPLETE"
    assert out["jm"]["score"] == 60.0


def test_rq_accepts_iso_datetime_as_of_and_created_at():
    from rn.resume_screening.scoring.scoring_engine_v3 import calc_rq

    resume = _resume()
    resume["resume_created_at"] = "2026-09-04T02:39:23"
    out = calc_rq(resume, as_of="2026-09-04T09:53:00+08:00")
    assert out["rq4_freshness_multiplier"] == 1.0
    assert out["score"] >= 0
