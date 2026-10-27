import json
from pathlib import Path

from rn.resume_screening.scoring.screening_pipeline_v3 import screen_candidate_v3, screen_candidates_v3


def job():
    return {
        "id": "J1", "title": "基础架构运维工程师", "location": "杭州",
        "hard_requirements": [{"name": "地点", "value": "杭州", "verify_type": "hard"}],
        "portrait_tags": [{"category": "capability", "tags": [
            {"name": "高可用架构", "weight": 1.0}
        ]}],
    }


def resume(candidate_id="C1"):
    return {
        "resume_id": candidate_id, "name": candidate_id, "education": {"level": "本科"},
        "location": "上海", "created_at": "2026-09-01", "source": "人才库",
        "raw_text": "负责 Keepalived 双机部署，通过 VIP 自动漂移实现故障节点切换。现居上海。",
        "work_experiences": [{
            "company": "甲公司", "position": "运维工程师", "start_date": "2022-01", "end_date": "至今",
            "description": "负责 Keepalived 双机部署，通过 VIP 自动漂移实现故障节点切换。",
        }],
    }


class FakeEvaluator:
    def evaluate(self, job_contract, facts):
        return {
            "schema_version": "3.0", "candidate_id": facts["candidate_id"], "job_id": job_contract["job_id"],
            "evaluation_status": "COMPLETE",
            "tag_assessments": [{
                "tag_id": job_contract["tags"][0]["tag_id"], "evidence_level": "CASE", "confidence": 0.93,
                "evidence": [{"text": "通过 VIP 自动漂移实现故障节点切换", "source_path": "raw_text"}],
                "reasoning_summary": "没有出现标签原词，但存在明确的高可用切换行为",
            }],
            "requirement_assessments": [{
                "requirement_id": job_contract["requirements"][0]["requirement_id"], "status": "UNKNOWN",
                "evidence": [], "reasoning_summary": "只知道现居上海，未说明是否接受杭州办公",
            }],
        }


class FailingEvaluator:
    def evaluate(self, *_args, **_kwargs):
        from rn.resume_screening.decision.semantic_evidence_evaluator import EvidenceEvaluationError
        raise EvidenceEvaluationError("LLM_REQUEST_FAILED", "timeout")


class HallucinatingEvaluator(FakeEvaluator):
    def evaluate(self, job_contract, facts):
        doc = super().evaluate(job_contract, facts)
        doc["tag_assessments"][0]["evidence"][0]["text"] = "将SLA提升到99.999%"
        return doc


def test_semantic_case_without_tag_keyword_scores_and_unknown_location_does_not_veto(tmp_path):
    result = screen_candidate_v3(resume(), job(), evaluator=FakeEvaluator(), trace_dir=tmp_path)
    assert result["scoring_status"] == "SCORING_COMPLETE"
    assert result["jm"]["score"] == 60.0
    assert result["veto"] is False
    assert result["needs_verification"]
    assert (tmp_path / "resume_facts.json").exists()
    assert (tmp_path / "semantic_evidence.json").exists()
    assert (tmp_path / "evidence_validation.json").exists()
    assert (tmp_path / "score_result.json").exists()
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["state"] == "SCORING_COMPLETE"


def test_llm_failure_produces_no_score_and_no_lexical_fallback(tmp_path):
    result = screen_candidate_v3(resume(), job(), evaluator=FailingEvaluator(), trace_dir=tmp_path)
    assert result["scoring_status"] == "EVIDENCE_EVALUATION_FAILED"
    assert result["jm"] is None and result["cs"] is None
    assert result["veto"] is False


def test_ungrounded_llm_quote_produces_no_score(tmp_path):
    result = screen_candidate_v3(resume(), job(), evaluator=HallucinatingEvaluator(), trace_dir=tmp_path)
    assert result["scoring_status"] == "EVIDENCE_GROUNDING_FAILED"
    assert result["jm"] is None and result["cs"] is None


def test_batch_report_separates_failures_from_ranked_candidates(tmp_path):
    evidence_dir = tmp_path / "precomputed"
    evidence_dir.mkdir()
    # Create one valid candidate evidence by using the same evaluator once via helper path.
    report = screen_candidates_v3([resume("C1"), resume("C2")], job(), evaluator=FakeEvaluator(), out_dir=tmp_path / "run", top=1)
    assert report["meta"]["engine_version"] == "3.1.0"
    assert len(report["recommendations"]) == 1
    assert report["meta"]["scoring_complete_count"] == 2


def test_replay_mode_missing_precomputed_evidence_fails_closed_without_live_llm(tmp_path, monkeypatch):
    monkeypatch.delenv("RECRUITER_LLM_API_KEY", raising=False)
    monkeypatch.delenv("RECRUITER_LLM_API_KEY_ENV", raising=False)
    evidence_dir = tmp_path / "empty-evidence"
    evidence_dir.mkdir()
    report = screen_candidates_v3([resume("C1")], job(), evidence_dir=evidence_dir, out_dir=tmp_path / "run2")
    assert report["meta"]["failed_count"] == 1
    failed = report["failed_candidates"][0]
    assert failed["scoring_status"] == "EVIDENCE_EVALUATION_FAILED"
    assert "PRECOMPUTED_EVIDENCE_MISSING" in failed["errors"]
