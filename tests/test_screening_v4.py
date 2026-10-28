from pathlib import Path

from rn.resume_screening.scoring.screening_v4 import HiringScreeningService, build_jd_contract_v4, score_candidate_v4


RESUME = {
    "resume_id": "C1",
    "name": "候选人甲",
    "raw_text": "负责临床项目并与客户沟通，完成科研数据分析。",
    "work_experiences": [{"company": "A", "position": "医学", "start_date": "2024-01-01", "end_date": "至今", "description": "负责临床项目并与客户沟通，完成科研数据分析。"}],
    "skills": [], "projects": [], "certifications": [], "education": {"level": "本科"},
}
PROFILE = {
    "profile_id": "PROFILE-MSL",
    "name": "医学联络官",
    "key_abilities": [{"name": "沟通表达", "def": "与客户沟通", "required": True}],
    "key_experiences": [{"name": "科研项目", "def": "科研项目经验", "required": True}],
    "key_traits": ["责任心"],
}
JD = {
    "schema_version": "4.0",
    "jd_id": "JD-REQ1",
    "request_id": "REQ1",
    "title": "医学联络官",
    "responsibilities": ["负责临床项目并与客户沟通"],
    "key_experience_skills": ["科研数据分析"],
    "hard_requirements": [],
    "bonus_points": [],
}


class FakeEvaluator:
    def evaluate(self, job, facts):
        assessments = []
        quote = "负责临床项目并与客户沟通，完成科研数据分析。"
        for tag in job["tags"]:
            level = "CASE" if job["job_id"].endswith("::PROFILE") else "QUANTIFIED_CASE"
            assessments.append({
                "tag_id": tag["tag_id"], "evidence_level": level, "confidence": 0.9,
                "evidence": [{"text": quote, "source_path": "raw_text"}], "reasoning_summary": "有具体行为",
            })
        reqs = []
        for req in job.get("requirements", []):
            reqs.append({"requirement_id": req["requirement_id"], "status": "UNKNOWN", "evidence": [], "reasoning_summary": "未明确"})
        return {
            "schema_version": "3.0", "candidate_id": facts["candidate_id"], "job_id": job["job_id"],
            "evaluation_status": "COMPLETE", "tag_assessments": assessments, "requirement_assessments": reqs,
        }


def test_jd_contract_has_semantic_tags_and_user_requirements():
    contract = build_jd_contract_v4("REQ1", JD)
    assert contract["job_id"] == "REQ1::JD"
    assert len(contract["tags"]) == 2
    assert round(sum(x["weight"] for x in contract["tags"]), 6) == 1.0


def test_candidate_v4_emits_separate_profile_and_jd_scores(tmp_path: Path):
    result = score_candidate_v4(RESUME, request_id="REQ1", jd=JD, profile=PROFILE, evaluator=FakeEvaluator(), trace_dir=tmp_path)
    assert result["scoring_status"] == "SCORING_COMPLETE"
    assert result["profile_match"]["score"] == 60.0
    assert result["jd_match"]["score"] == 100.0
    assert result["jm"]["score"] == 84.0
    assert result["blend"]["profile_weight"] == 0.4
    assert (tmp_path / "profile_semantic_evidence.json").exists()
    assert (tmp_path / "jd_semantic_evidence.json").exists()
    assert (tmp_path / "semantic_evidence.json").exists()


def test_candidate_without_profile_uses_jd_only(tmp_path: Path):
    result = score_candidate_v4(RESUME, request_id="REQ1", jd=JD, profile=None, evaluator=FakeEvaluator(), trace_dir=tmp_path)
    assert result["profile_match"] is None
    assert result["jd_match"]["score"] == 100.0
    assert result["jm"]["score"] == 100.0
    assert result["blend"]["profile_weight"] == 0.0


class FakeResumeRepo:
    def count(self): return 5
    def search_candidates(self, query="", limit=50, **kwargs):
        return [dict(RESUME, resume_id=f"C{i}", name=f"候选人{i}") for i in range(1, 6)][:limit]

class FakeProfileRecord:
    profile_id = "PROFILE-MSL"; name = "医学联络官"; raw = PROFILE
class FakeProfileRepo:
    def get(self, key): return FakeProfileRecord() if key == "PROFILE-MSL" else None
class FakeJDRepo: pass


def test_ranking_scores_full_retrieved_pool_not_only_first_three(tmp_path: Path):
    service = HiringScreeningService(
        resume_repo=FakeResumeRepo(), profile_repo=FakeProfileRepo(), jd_repo=FakeJDRepo(), evaluator=FakeEvaluator(),
        run_root=tmp_path, pool_limit=5,
    )
    request = {
        "request_id": "REQ1", "position_title": "医学联络官",
        "request": {"fields": {"position_title": "医学联络官"}, "profile_match": {"status": "MATCHED", "profile_id": "PROFILE-MSL"}},
        "jd": JD,
    }
    result = service.rank_request(request)
    assert len(result["ranking"]) == 5
    assert result["resume_count"] == 5
    assert all("profile_match" in x and "jd_match" in x for x in result["ranking"])
