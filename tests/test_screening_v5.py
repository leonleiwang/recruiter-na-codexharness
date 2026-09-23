import json
from pathlib import Path

import pytest

from rn.screening_v5 import (
    HiringDecisionScreeningService,
    UnsupportedScreeningPolicy,
    evaluate_candidate_v5,
)


class FakeProfileRecord:
    profile_id = "PROFILE-412167F2937C"
    name = "检验技术员（社招）"
    raw = {"profile_id": profile_id, "name": name}


class FakeProfileRepo:
    def get(self, key):
        return FakeProfileRecord() if key == FakeProfileRecord.profile_id else None


class FakeResumeRepo:
    def __init__(self, count=5):
        self.rows = []
        for i in range(1, count + 1):
            raw = f"候选人{i} 本科 医学检验技术 持检验师证 杭州 检验科3年"
            if i == 2:
                raw += " 带教2名新人"
            if i == 3:
                raw += " 地点意愿未说明"
            if i == 4:
                raw += " 检验师证已过期"
            self.rows.append({
                "candidate_id": f"C{i}", "resume_id": f"C{i}", "name": f"候选人{i}",
                "education": {"level": "本科", "major": "医学检验技术"},
                "location": "杭州", "certifications": ["检验师证"],
                "total_work_years": 3, "current_position": "检验技术员",
                "raw_text": raw,
            })

    def count(self):
        return len(self.rows)

    def search_candidates(self, query="", limit=50, **kwargs):
        return self.rows[:limit]


class FakeEvaluator:
    def evaluate(self, criteria, facts):
        cid = facts["candidate_id"]
        if cid == "C5":
            raise RuntimeError("synthetic evaluator outage")

        raw = facts["raw_text"]
        location_status = "unknown" if cid == "C3" else "match"
        cert_status = "expired" if cid == "C4" else "valid"
        hard = {
            "education": {"status": "matched", "evidence": [{"text": "本科 医学检验技术", "source_path": "raw_text"}], "reasoning_summary": "符合"},
            "certification": {"status": cert_status, "evidence": [{"text": "检验师证已过期" if cid == "C4" else "持检验师证", "source_path": "raw_text"}], "reasoning_summary": cert_status},
            "location": {"status": location_status, "evidence": [] if location_status == "unknown" else [{"text": "杭州", "source_path": "raw_text"}], "reasoning_summary": location_status},
        }
        rows = []
        for criterion in criteria["criteria"]:
            mode = criterion["decision_mode"]
            name = criterion["name"]
            if mode == "resume_evidence":
                result, status, quote = "satisfied", "confirmed", "检验科3年"
            elif mode == "preference" and name == "人员带教经验" and cid == "C2":
                result, status, quote = "satisfied", "confirmed", "带教2名新人"
            else:
                result, status, quote = "unknown", "none", ""
            rows.append({
                "criterion_id": criterion["criterion_id"],
                "criterion_result": result,
                "evidence_status": status,
                "evidence": [{"text": quote, "source_path": "raw_text"}] if quote else [],
                "reasoning_summary": f"{name}:{result}",
            })
        return {
            "schema_version": "5.0",
            "candidate_id": cid,
            "policy_id": criteria["policy_id"],
            "evaluation_status": "COMPLETE",
            "hard_checks": hard,
            "red_line_assessments": [
                {"red_line_id": f"RL{i:02d}", "status": "clear", "evidence": [], "reasoning_summary": "未命中"}
                for i, _ in enumerate(criteria["hard_filters"]["red_lines"], 1)
            ],
            "criteria_assessments": rows,
        }


def request(title="检验技术员", profile_id="PROFILE-412167F2937C"):
    return {
        "request_id": "REQ1", "position_title": title,
        "request": {
            "fields": {"position_title": title, "location": "杭州", "headcount": 1, "extra_hard_requirements": []},
            "profile_match": {"status": "MATCHED", "profile_id": profile_id},
        },
        "jd": {"title": title, "responsibilities": ["检验"], "key_experience_skills": ["检验科经验"], "hard_requirements": []},
    }


def test_single_candidate_pipeline_has_decision_and_no_formal_scores(tmp_path: Path):
    resume = FakeResumeRepo(1).rows[0]
    result = evaluate_candidate_v5(
        resume, request=request(), profile=FakeProfileRecord(), evaluator=FakeEvaluator(), trace_dir=tmp_path,
    )
    assert result["decision_status"] == "DECISION_COMPLETE"
    assert result["decision"] == "pass"
    blob = json.dumps(result, ensure_ascii=False).lower()
    for forbidden in ('"rq"', '"jm"', '"cs"', 'profile_match', 'jd_match', 'scoring_status'):
        assert forbidden not in blob
    assert (tmp_path / "resume_facts.json").exists()
    assert (tmp_path / "decision_evidence.json").exists()
    assert (tmp_path / "decision_evidence_validation.json").exists()
    assert (tmp_path / "candidate_decision.json").exists()


def test_service_evaluates_full_pool_orders_passes_and_isolates_rejections_and_technical_failures(tmp_path: Path):
    service = HiringDecisionScreeningService(
        resume_repo=FakeResumeRepo(5), profile_repo=FakeProfileRepo(), jd_repo=object(), evaluator=FakeEvaluator(),
        run_root=tmp_path, pool_limit=5,
    )
    result = service.screen_request(request())
    assert result["resume_count"] == 5
    assert result["candidate_pool_count"] == 5
    assert [x["candidate_id"] for x in result["recommendations"]] == ["C2", "C1", "C3"]
    assert result["recommendations"][0]["recommendation_order"] == 1
    assert result["recommendations"][2]["readiness"] == "needs_verification"
    # 社招画像：6 能力 + 3 经历 + 4 特质 = 13 条判据，加学历/资格证/地点 3 个资格标签。
    assert result["recommendations"][0]["applicable_tag_count"] == 16
    assert result["recommendations"][0]["matched_tag_count"] == 6
    assert [x["candidate_id"] for x in result["rejected_candidates"]] == ["C4"]
    assert [x["candidate_id"] for x in result["failures"]] == ["C5"]
    assert "score" not in json.dumps(result["recommendations"], ensure_ascii=False).lower()
    run_dir = Path(result["trace_dir"])
    assert (run_dir / "decision_policy.json").exists()
    assert (run_dir / "actual_criteria.json").exists()
    actual = json.loads((run_dir / "actual_criteria.json").read_text(encoding="utf-8"))
    assert len(actual["criteria"]) == 13
    assert [x["name"] for x in actual["profile_criterion_exclusions"]] == ["专业理论知识", "专业背景与项目经验", "持续学习"]
    assert (run_dir / "decision_snapshot.json").exists()


def test_default_interactive_pool_is_bounded_for_large_resume_libraries(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("RECRUITER_SCREEN_POOL_LIMIT", raising=False)
    monkeypatch.delenv("RECRUITER_DECISION_WORKERS", raising=False)
    service = HiringDecisionScreeningService(
        resume_repo=FakeResumeRepo(50), profile_repo=FakeProfileRepo(), jd_repo=object(), evaluator=FakeEvaluator(),
        run_root=tmp_path,
    )

    assert service.pool_limit == 12



class QuerySensitiveResumeRepo(FakeResumeRepo):
    def search_candidates(self, query="", limit=50, **kwargs):
        # Simulate the production MySQL post-filter: MinerU rows may contain
        # 医学检验/检验科 but not the exact four-character title 检验技术员.
        if query == "检验技术员" or query == "检验技术员（社招）":
            return [self.rows[0]][:limit]
        if query in {"医学检验", "检验科", "检验"}:
            return self.rows[:limit]
        return []


def test_pool_uses_policy_recall_aliases_so_mineru_rows_are_not_lost(tmp_path: Path):
    repo = QuerySensitiveResumeRepo(5)
    service = HiringDecisionScreeningService(
        resume_repo=repo, profile_repo=FakeProfileRepo(), jd_repo=object(), evaluator=FakeEvaluator(),
        run_root=tmp_path, pool_limit=5,
    )
    profile = FakeProfileRecord()
    pool = service._pool(request(), profile)
    assert len(pool) == 5
    assert {x["candidate_id"] for x in pool} == {"C1", "C2", "C3", "C4", "C5"}

def test_unsupported_role_refuses_screening_without_score_fallback(tmp_path: Path):
    service = HiringDecisionScreeningService(
        resume_repo=FakeResumeRepo(1), profile_repo=FakeProfileRepo(), jd_repo=object(), evaluator=FakeEvaluator(), run_root=tmp_path,
    )
    with pytest.raises(UnsupportedScreeningPolicy) as exc:
        service.screen_request(request(title="医学联络官", profile_id="PROFILE-2FC5FDEBF75E"))
    assert exc.value.code == "SCREENING_POLICY_UNSUPPORTED_ROLE"



def test_candidate_workers_are_actually_concurrent(tmp_path: Path):
    import threading

    class BarrierEvaluator(FakeEvaluator):
        def __init__(self):
            self.barrier = threading.Barrier(3)
            self.lock = threading.Lock()
            self.active = 0
            self.max_active = 0

        def evaluate(self, criteria, facts):
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            try:
                self.barrier.wait(timeout=2.0)
                return super().evaluate(criteria, facts)
            finally:
                with self.lock:
                    self.active -= 1

    evaluator = BarrierEvaluator()
    service = HiringDecisionScreeningService(
        resume_repo=FakeResumeRepo(3), profile_repo=FakeProfileRepo(), jd_repo=object(), evaluator=evaluator,
        run_root=tmp_path, pool_limit=3,
    )
    result = service.screen_request(request())
    assert result["technical_failure_count"] == 0
    assert evaluator.max_active == 3


def test_screening_writes_candidate_and_aggregate_latency_traces(tmp_path: Path):
    service = HiringDecisionScreeningService(
        resume_repo=FakeResumeRepo(2), profile_repo=FakeProfileRepo(), jd_repo=object(), evaluator=FakeEvaluator(),
        run_root=tmp_path, pool_limit=2,
    )
    result = service.screen_request(request())
    assert result["performance"]["worker_count"] >= 1
    assert result["performance"]["screening_elapsed_ms"] >= 0
    run_dir = Path(result["trace_dir"])
    for cid in ("C1", "C2"):
        timing = json.loads((run_dir / "candidates" / cid / "timing.json").read_text(encoding="utf-8"))
        assert timing["total_ms"] >= 0
        assert timing["llm_evaluation_ms"] >= 0
        assert "grounding_repair_ms" in timing


def test_production_regression_ungrounded_model_quote_becomes_verify_not_technical_failure(tmp_path: Path):
    class UngroundedEvaluator(FakeEvaluator):
        def evaluate(self, criteria, facts):
            doc = super().evaluate(criteria, facts)
            # Reproduce v5.0.0 production symptom: model returned a definitive
            # criterion with text/path that cannot be grounded in resume_facts.
            first = doc["criteria_assessments"][0]
            first.update({
                "criterion_result": "not_satisfied",
                "evidence_status": "confirmed",
                "evidence": [{"text": "仅有6个月检验科经验", "source_path": "work_experiences[99].description"}],
                "reasoning_summary": "不足1年",
            })
            return doc

    service = HiringDecisionScreeningService(
        resume_repo=FakeResumeRepo(1), profile_repo=FakeProfileRepo(), jd_repo=object(), evaluator=UngroundedEvaluator(),
        run_root=tmp_path, pool_limit=1,
    )
    result = service.screen_request(request())
    assert result["technical_failure_count"] == 0
    assert result["pass_count"] == 1
    assert result["recommendations"][0]["readiness"] == "needs_verification"
    run_dir = Path(result["trace_dir"])
    repair = json.loads((run_dir / "candidates" / "C1" / "grounding_repair.json").read_text(encoding="utf-8"))
    assert any(x["action"] == "downgraded_criterion_to_unknown" for x in repair["repairs"])
