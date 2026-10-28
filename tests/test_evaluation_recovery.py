"""Synthetic model contract failures must remain recoverable, never disappear."""
import json
from pathlib import Path
from collections import Counter

import pytest

from rn.hiring_requests import HiringRequestRepository
from rn.resume_screening.decision.screening_v5 import HiringDecisionScreeningService
from test_hiring_workflow_v5 import FakeAnalyzer, make_workflow, _ready
from test_screening_v5 import FakeEvaluator, FakeProfileRepo, FakeResumeRepo

OWNER = "dingtalk:alice"


class RetryAnalyzer(FakeAnalyzer):
    def _call(self, prompt):
        if "用户消息: 查看我的招聘进展" in prompt:
            return {"intent": "show_progress"}
        if "用户消息: 重试未完成" in prompt:
            return {"intent": "retry_pending"}
        return super()._call(prompt)


class SyntheticResumes(FakeResumeRepo):
    def __init__(self):
        sample = FakeResumeRepo(1).rows[0]
        self.rows = [{**sample, "candidate_id": f"C{i}", "resume_id": f"C{i}",
                      "name": f"合成{i}"} for i in range(1, 7)]

    def get(self, identifier):
        return next((x for x in self.rows if x["resume_id"] == identifier), None)


class ModelResponses(FakeEvaluator):
    def __init__(self):
        self.broken = True
        self.failed_ids = {"C6"}
        self.expired = False
        self.calls = Counter()

    def evaluate(self, criteria, facts):
        cid = facts["candidate_id"]
        self.calls[cid] += 1
        doc = super().evaluate(criteria, {**facts, "candidate_id": "C4" if self.expired else "C1"})
        doc["candidate_id"] = cid
        if cid in self.failed_ids and self.broken:
            doc["red_line_assessments"] = []  # actual strict validator rejects this
        return doc


def setup_case(tmp_path):
    model, resumes = ModelResponses(), SyntheticResumes()
    screening = HiringDecisionScreeningService(resume_repo=resumes, profile_repo=FakeProfileRepo(),
        jd_repo=object(), evaluator=model, run_root=tmp_path / "runs", pool_limit=6)
    wf = make_workflow(tmp_path, screening=screening, analyzer=RetryAnalyzer())
    _ready(wf)
    first = wf.handle(OWNER, "开始筛选")
    assert first["decision_summary"] == {"pass_count": 5, "reject_count": 0, "technical_failure_count": 1}
    snapshot = json.loads((Path(first["trace_dir"]) / "decision_snapshot.json").read_text())
    assert snapshot["failures"][0]["error"] == "red line assessment count mismatch"
    return wf, model, resumes, first


def ids(reply):
    return [x["candidate_id"] for x in reply.get("candidates", [])]


def exhaust_successes(wf, first):
    second = wf.handle(OWNER, "换一批")
    assert len(ids(first)) == 3 and len(ids(second)) == 2
    shown = ids(first) + ids(second)
    assert len(set(shown)) == 5
    return shown, wf.handle(OWNER, "换一批")


def test_incomplete_evaluation_is_not_pool_exhaustion_and_survives_restart(tmp_path):
    wf, model, _, first = setup_case(tmp_path)
    shown, empty = exhaust_successes(wf, first)
    assert empty["state"] == "EVALUATION_PENDING"
    assert "1" in empty["message"] and "未完成" in empty["message"]
    assert "全部推荐完" not in empty["message"]
    repo = HiringRequestRepository(tmp_path / "req.sqlite3")
    saved = repo.get_for_owner(OWNER, first["request_id"])
    assert saved["request"]["pending_evaluations"][0]["candidate_id"] == "C6"
    assert saved["recommended_candidate_ids"] == shown
    assert "未完成" in wf.handle(OWNER, "查看我的招聘进展")["message"]
    assert all(n == 1 for n in model.calls.values())


@pytest.mark.parametrize("recovered", [False, True])
def test_retry_only_failed_candidate_preserves_order_seen_and_exhaustion(tmp_path, recovered):
    wf, model, _, first = setup_case(tmp_path)
    shown, _ = exhaust_successes(wf, first)
    model.broken = not recovered
    wf = make_workflow(tmp_path, screening=wf.screening, analyzer=RetryAnalyzer())
    reply = wf.handle(OWNER, "重试未完成")
    assert model.calls == Counter({"C6": 2, **{f"C{i}": 1 for i in range(1, 6)}})
    saved = wf.requests.get_for_owner(OWNER, first["request_id"])
    assert [x["candidate_id"] for x in saved["ranking"]][:5] == shown
    if recovered:
        assert ids(reply) == ["C6"]
        assert saved["request"]["pending_evaluations"] == []
        assert wf.handle(OWNER, "重试未完成")["candidates"] == []
        assert model.calls["C6"] == 2
        assert wf.handle(OWNER, "换一批")["state"] == "WAITING_HR_SOURCING"
    else:
        assert reply["state"] == "EVALUATION_PENDING"
        assert saved["request"]["pending_evaluations"][0]["attempts"] == 2
        assert "mismatch" not in reply["message"]
        assert saved["recommended_candidate_ids"] == shown


def test_retry_before_existing_batch_consumed_appends_without_reordering(tmp_path):
    wf, model, _, first = setup_case(tmp_path)
    original = wf.requests.get_for_owner(OWNER, first["request_id"])["ranking"]
    model.broken = False
    reply = wf.handle(OWNER, "重试未完成")
    saved = wf.requests.get_for_owner(OWNER, first["request_id"])
    assert saved["ranking"][:5] == original
    assert set(ids(reply)) == {"C4", "C5", "C6"}
    assert not set(ids(first)).intersection(ids(reply))


def test_missing_source_and_foreign_owner_cannot_consume_pending(tmp_path):
    wf, model, resumes, first = setup_case(tmp_path)
    exhaust_successes(wf, first)
    wf.handle("dingtalk:bob", "重试未完成")
    assert model.calls["C6"] == 1
    with pytest.raises(PermissionError):
        wf.requests.set_recommendations_for_owner("dingtalk:bob", first["request_id"], [], resume_count=6)
    assert wf.requests.get_for_owner("dingtalk:bob", first["request_id"]) is None
    resumes.rows = resumes.rows[:5]
    reply = wf.handle(OWNER, "重试未完成")
    assert reply["state"] == "EVALUATION_PENDING" and model.calls["C6"] == 1


def test_backend_failure_leaves_pending_and_never_replays_successes(tmp_path):
    wf, model, resumes, first = setup_case(tmp_path)
    shown, _ = exhaust_successes(wf, first)
    def offline():
        raise RuntimeError("synthetic backend unavailable")
    resumes.count = offline
    reply = wf.handle(OWNER, "重试未完成")
    saved = wf.requests.get_for_owner(OWNER, first["request_id"])
    assert saved["request"]["pending_evaluations"][0]["candidate_id"] == "C6"
    assert saved["recommended_candidate_ids"] == shown
    assert "backend unavailable" not in reply["message"]
    assert all(n == 1 for n in model.calls.values())


def test_retry_cannot_substitute_a_different_resume(tmp_path):
    wf, model, resumes, first = setup_case(tmp_path)
    exhaust_successes(wf, first)
    resumes.get = lambda identifier: resumes.rows[0]
    result = wf.handle(OWNER, "重试未完成")
    assert result["state"] == "EVALUATION_PENDING"
    assert all(n == 1 for n in model.calls.values())


def test_repeated_result_persistence_is_idempotent(tmp_path):
    wf, model, _, first = setup_case(tmp_path)
    exhaust_successes(wf, first)
    model.broken = False
    retry = wf.handle(OWNER, "重试未完成")
    before = wf.requests.get_for_owner(OWNER, first["request_id"])
    for _ in range(2):
        wf.requests.set_recommendations_for_owner(OWNER, first["request_id"], retry["candidates"],
            resume_count=6, append=True, pending_evaluations=[])
    saved = wf.requests.get_for_owner(OWNER, first["request_id"])
    assert saved["ranking"] == before["ranking"]
    assert saved["recommended_candidate_ids"] == before["recommended_candidate_ids"]
    assert wf.requests.next_batch(OWNER, first["request_id"]) == []


def test_select_first_after_one_candidate_retry_selects_that_candidate(tmp_path):
    wf, model, _, first = setup_case(tmp_path)
    exhaust_successes(wf, first)
    model.broken = False
    assert ids(wf.handle(OWNER, "重试未完成")) == ["C6"]
    wf.handle(OWNER, "第一个")
    saved = wf.requests.get_for_owner(OWNER, first["request_id"])
    assert saved["selected_candidate_id"] == "C6"


def test_all_failed_and_reduced_retry_limit_keep_unattempted_candidates(tmp_path):
    model, resumes = ModelResponses(), SyntheticResumes()
    model.failed_ids = {f"C{i}" for i in range(1, 7)}
    screening = HiringDecisionScreeningService(resume_repo=resumes, profile_repo=FakeProfileRepo(),
        jd_repo=object(), evaluator=model, run_root=tmp_path / "runs", pool_limit=6)
    wf = make_workflow(tmp_path, screening=screening, analyzer=RetryAnalyzer())
    _ready(wf)
    first = wf.handle(OWNER, "开始筛选")
    assert first["state"] == "EVALUATION_PENDING" and ids(first) == []
    model.broken = False
    screening.pool_limit = 2
    reply = wf.handle(OWNER, "重试未完成")
    assert len(ids(reply)) == 2
    saved = wf.requests.get_for_owner(OWNER, first["request_id"])
    assert len(saved["request"]["pending_evaluations"]) == 4
    assert sorted(model.calls.values()) == [1, 1, 1, 1, 2, 2]


def test_retry_business_rejection_resolves_pending_without_recommendation(tmp_path):
    wf, model, resumes, first = setup_case(tmp_path)
    exhaust_successes(wf, first)
    model.broken, model.expired = False, True
    resumes.rows[-1]["raw_text"] += " 检验师证已过期"
    reply = wf.handle(OWNER, "重试未完成")
    saved = wf.requests.get_for_owner(OWNER, first["request_id"])
    assert saved["request"]["pending_evaluations"] == []
    assert ids(reply) == [] and reply["state"] == "WAITING_HR_SOURCING"
    assert len(saved["ranking"]) == 5
