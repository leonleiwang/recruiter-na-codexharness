import copy

from rn.resume_screening.scoring.scoring_engine_v3 import calc_rq2_continuity, score_candidate


def base_resume():
    return {
        "candidate_id": "C001",
        "name": "候选人A",
        "degree": "本科",
        "resume_created_at": "2026-08-20",
        "resume_source": "主动投递",
        "raw_text": (
            "建设双活架构，将核心服务可用性提升至99.95%。"
            "负责线上故障定位、复盘与改进。明确注明未取得该资格。"
        ),
        "work_experiences": [
            {
                "company": "甲公司",
                "position": "平台工程师",
                "start_date": "2022-01",
                "end_date": "2024-01",
                "description": "建设双活架构，将核心服务可用性提升至99.95%",
            },
            {
                "company": "乙公司",
                "position": "SRE",
                "start_date": "2024-02",
                "end_date": "至今",
                "description": "负责线上故障定位、复盘与改进",
            },
        ],
    }


def base_job():
    return {
        "schema_version": "3.0",
        "job_id": "J001",
        "job_name": "平台工程师",
        "tags": [
            {"tag_id": "T1", "name": "高可用架构", "category": "capability", "weight": 0.5},
            {"tag_id": "T2", "name": "故障响应", "category": "experience", "weight": 0.5},
        ],
        "requirements": [],
    }


def base_evidence():
    return {
        "schema_version": "3.0",
        "candidate_id": "C001",
        "job_id": "J001",
        "evaluation_status": "COMPLETE",
        "tag_assessments": [
            {
                "tag_id": "T1",
                "evidence_level": "QUANTIFIED_CASE",
                "confidence": 0.95,
                "evidence": [{
                    "text": "建设双活架构，将核心服务可用性提升至99.95%",
                    "source_path": "work_experiences[0].description",
                }],
                "reasoning_summary": "有明确项目与量化结果",
            },
            {
                "tag_id": "T2",
                "evidence_level": "CASE",
                "confidence": 0.9,
                "evidence": [{
                    "text": "负责线上故障定位、复盘与改进",
                    "source_path": "work_experiences[1].description",
                }],
                "reasoning_summary": "有实际故障处理案例但未量化",
            },
        ],
        "requirement_assessments": [],
    }


def test_missing_semantic_evidence_refuses_scoring():
    out = score_candidate(base_resume(), base_job(), None, as_of="2026-09-03")
    assert out["scoring_status"] == "EVIDENCE_EVALUATION_FAILED"
    assert out["jm"] is None and out["cs"] is None


def test_unassessable_tag_refuses_scoring():
    ev = base_evidence()
    ev["tag_assessments"][0]["evidence_level"] = "UNASSESSABLE"
    out = score_candidate(base_resume(), base_job(), ev, as_of="2026-09-03")
    assert out["scoring_status"] == "EVIDENCE_EVALUATION_FAILED"
    assert out["jm"] is None


def test_four_level_weighting_is_deterministic():
    out = score_candidate(base_resume(), base_job(), base_evidence(), as_of="2026-09-03")
    assert out["scoring_status"] == "SCORING_COMPLETE"
    assert out["jm"]["score"] == 80.0
    assert out["jm"]["tag_details"][0]["ratio"] == 1.0
    assert out["jm"]["tag_details"][1]["ratio"] == 0.6


def test_no_evidence_is_valid_zero_not_failure():
    ev = base_evidence()
    ev["tag_assessments"][1]["evidence_level"] = "NO_EVIDENCE"
    ev["tag_assessments"][1]["evidence"] = []
    ev["tag_assessments"][1]["reasoning_summary"] = "简历中未找到支持证据"
    out = score_candidate(base_resume(), base_job(), ev, as_of="2026-09-03")
    assert out["scoring_status"] == "SCORING_COMPLETE"
    assert out["jm"]["score"] == 50.0


def test_positive_level_requires_evidence_text():
    ev = base_evidence()
    ev["tag_assessments"][0]["evidence"] = []
    out = score_candidate(base_resume(), base_job(), ev, as_of="2026-09-03")
    assert out["scoring_status"] == "EVIDENCE_EVALUATION_FAILED"
    assert "EVIDENCE_REQUIRED" in out["errors"]


def test_job_and_candidate_ids_must_match():
    ev = base_evidence()
    ev["candidate_id"] = "OTHER"
    out = score_candidate(base_resume(), base_job(), ev, as_of="2026-09-03")
    assert out["scoring_status"] == "CONTRACT_INVALID"
    assert "CANDIDATE_ID_MISMATCH" in out["errors"]


def test_weights_must_sum_to_one():
    job = base_job()
    job["tags"][0]["weight"] = 0.2
    out = score_candidate(base_resume(), job, base_evidence(), as_of="2026-09-03")
    assert out["scoring_status"] == "CONTRACT_INVALID"
    assert "TAG_WEIGHT_SUM_INVALID" in out["errors"]


def test_rq2_uses_actual_career_gap_not_job_count():
    no_gap = base_resume()
    long_gap = copy.deepcopy(no_gap)
    long_gap["work_experiences"][1]["start_date"] = "2025-02"
    score_no_gap, months_no_gap = calc_rq2_continuity(no_gap, as_of="2026-09-03")
    score_long_gap, months_long_gap = calc_rq2_continuity(long_gap, as_of="2026-09-03")
    assert (months_no_gap, score_no_gap) == (0, 20)
    assert (months_long_gap, score_long_gap) == (12, 12)


def test_resume_freshness_is_applied_except_active_application():
    resume = base_resume()
    resume["resume_created_at"] = "2025-01-01"
    resume["resume_source"] = "人才库"
    out = score_candidate(resume, base_job(), base_evidence(), as_of="2026-09-03")
    assert out["rq"]["rq4_freshness_multiplier"] == 0.7
    resume["resume_source"] = "主动投递"
    out2 = score_candidate(resume, base_job(), base_evidence(), as_of="2026-09-03")
    assert out2["rq"]["rq4_freshness_multiplier"] == 1.0


def test_explicit_hard_gate_fail_vetoes_only_with_grounded_quote():
    job = base_job()
    job["requirements"] = [{
        "requirement_id": "R1", "name": "必须具备执业资格", "gate_type": "HARD_GATE"
    }]
    ev = base_evidence()
    ev["requirement_assessments"] = [{
        "requirement_id": "R1", "status": "FAIL",
        "evidence": [{"text": "明确注明未取得该资格", "source_path": "raw_text"}],
        "reasoning_summary": "明确不满足",
    }]
    out = score_candidate(base_resume(), job, ev, as_of="2026-09-03")
    assert out["scoring_status"] == "SCORING_COMPLETE"
    assert out["veto"] is True and out["cs"] == -1


def test_unknown_hard_gate_is_verification_not_veto():
    job = base_job()
    job["requirements"] = [{
        "requirement_id": "R1", "name": "是否接受杭州现场办公", "gate_type": "HARD_GATE"
    }]
    ev = base_evidence()
    ev["requirement_assessments"] = [{
        "requirement_id": "R1", "status": "UNKNOWN", "evidence": [],
        "reasoning_summary": "只知道当前城市，未说明是否接受异地机会",
    }]
    out = score_candidate(base_resume(), job, ev, as_of="2026-09-03")
    assert out["scoring_status"] == "SCORING_COMPLETE"
    assert out["veto"] is False
    assert out["needs_verification"] == ["R1"]
    assert out["cs"] >= 0


def test_low_rq_and_low_jm_do_not_create_functional_veto():
    resume = base_resume()
    resume["work_experiences"] = [{
        "company": "甲", "position": "实习", "start_date": "2026-01", "end_date": "2026-02",
        "description": "短期经历",
    }]
    ev = base_evidence()
    for item in ev["tag_assessments"]:
        item["evidence_level"] = "NO_EVIDENCE"
        item["evidence"] = []
        item["reasoning_summary"] = "未找到证据"
    out = score_candidate(resume, base_job(), ev, as_of="2026-09-03")
    assert out["jm"]["score"] == 0.0
    assert out["veto"] is False
    assert out["cs"] >= 0


def test_engine_accepts_arbitrary_tag_names_without_keyword_branches():
    job = base_job()
    job["tags"][0]["name"] = "一种从未写进引擎的业务能力"
    out = score_candidate(base_resume(), job, base_evidence(), as_of="2026-09-03")
    assert out["scoring_status"] == "SCORING_COMPLETE"
    assert out["jm"]["score"] == 80.0
