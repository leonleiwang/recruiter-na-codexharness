from pathlib import Path

from rn.hiring_requests import HiringRequestRepository
from rn.hiring_workflow_v5 import HiringManagerWorkflow
from rn.screening_v5 import UnsupportedScreeningPolicy


class FakeProfileRepo:
    def __init__(self):
        self.profile = type("P", (), {"profile_id": "PROFILE-412167F2937C", "name": "检验技术员（社招）", "raw": {
            "profile_id": "PROFILE-412167F2937C", "name": "检验技术员（社招）",
            "description": "检验技术员标准画像",
            "key_abilities": [{"name": "规范操作与执行能力", "def": "按SOP操作", "required": True}],
            "key_experiences": [{"name": "检验科经验", "def": "1年以上", "required": True}],
            "key_traits": ["责任心"],
        }})()

    def get(self, key):
        return self.profile if key == self.profile.profile_id else None

    def all(self):
        return [self.profile]

    def list_all(self):
        return [self.profile]


class FakeJDRepo:
    def search_related(self, query):
        return []


class FakeResumeRepo:
    def count(self):
        return 146


class FakeAnalyzer:
    def analyze_initial(self, text, profiles):
        return {
            "fields": {"position_title": "检验技术员", "location": "杭州", "headcount": 1},
            "provided_fields": ["position_title", "location", "headcount"],
            "profile_match": {"status": "MATCHED", "profile_id": "PROFILE-412167F2937C", "confidence": 0.96, "reason": "职责一致"},
        }

    def analyze_update(self, text, request, missing_fields):
        return {
            "fields": {"department": "实验室部", "salary_range": "10-13K", "urgency": "尽快", "extra_hard_requirements": []},
            "provided_fields": ["department", "salary_range", "urgency", "extra_hard_requirements"],
        }


class FakeScreening:
    def screen_request(self, request):
        return {
            "resume_count": 146,
            "recommendations": [
                {"recommendation_order": 1, "candidate_id": "C1", "name": "甲", "decision": "pass", "readiness": "ready", "recommendation_reasons": ["检验科经验：3年三甲医院检验科"], "needs_verification": [], "interview_focus": [{"item": "沟通表达能力", "reason": "面试确认"}], "preference_signals": [], "summary": {"degree": "本科", "total_work_years": 3, "location": "杭州"}},
                {"recommendation_order": 2, "candidate_id": "C2", "name": "乙", "decision": "pass", "readiness": "ready", "recommendation_reasons": ["人员带教经验：带教2人"], "needs_verification": [], "interview_focus": [], "preference_signals": [{"item": "人员带教经验", "evidence": "带教2人"}], "summary": {"degree": "本科", "total_work_years": 4, "location": "杭州"}},
                {"recommendation_order": 3, "candidate_id": "C3", "name": "丙", "decision": "pass", "readiness": "needs_verification", "recommendation_reasons": ["检验科经验：2年"], "needs_verification": [{"item": "检验资格证", "reason": "简历未提及证书"}], "interview_focus": [], "preference_signals": [], "summary": {"degree": "大专", "total_work_years": 2, "location": "宁波"}},
                {"recommendation_order": 4, "candidate_id": "C4", "name": "丁", "decision": "pass", "readiness": "ready", "recommendation_reasons": ["检验科经验：5年"], "needs_verification": [], "interview_focus": [], "preference_signals": [], "summary": {"degree": "本科", "total_work_years": 5, "location": "杭州"}},
            ],
            "trace_dir": "workspace/runs/test-v5",
        }


class UnsupportedScreening:
    def screen_request(self, request):
        raise UnsupportedScreeningPolicy()


def make_workflow(tmp_path: Path, screening=None):
    return HiringManagerWorkflow(
        request_repo=HiringRequestRepository(tmp_path / "req.sqlite3"),
        profile_repo=FakeProfileRepo(), jd_repo=FakeJDRepo(), resume_repo=FakeResumeRepo(),
        analyzer=FakeAnalyzer(), screening_service=screening or FakeScreening(),
    )


def _ready(wf):
    wf.handle("dingtalk:alice", "杭州招一个检验技术员，1人")
    return wf.handle("dingtalk:alice", "实验室部，10-13K，尽快，没有额外硬要求")


def test_matched_profile_hides_recruiter_internal_decision_modes_from_manager(tmp_path: Path):
    wf = make_workflow(tmp_path)
    reply = wf.handle("dingtalk:alice", "杭州招一个检验技术员，1人")
    assert "已匹配" in reply["message"]
    assert "部门" in reply["message"]
    assert "薪资" in reply["message"]
    assert "decision_mode" not in reply["message"]
    assert "hard_gate" not in reply["message"]
    assert "evidence" not in reply["message"].lower()
    assert "核心职责" not in reply["message"]


def test_screening_renders_candidate_decisions_without_score_fields(tmp_path: Path):
    wf = make_workflow(tmp_path)
    ready = _ready(wf)
    assert ready["state"] == "JD_READY"
    out = wf.handle("dingtalk:alice", "开始筛选")
    assert [x["candidate_id"] for x in out["candidates"]] == ["C1", "C2", "C3"]
    assert "可直接推荐" in out["message"]
    assert "需确认" in out["message"]
    assert "推荐依据" in out["message"]
    for forbidden in ("CS ", "RQ ", "JM ", "画像匹配", "JD 匹配"):
        assert forbidden not in out["message"]


def test_next_batch_keeps_no_repeat_state_machine(tmp_path: Path):
    wf = make_workflow(tmp_path)
    _ready(wf)
    first = wf.handle("dingtalk:alice", "开始筛选")
    second = wf.handle("dingtalk:alice", "换一批")
    third = wf.handle("dingtalk:alice", "再换一批")
    assert [x["candidate_id"] for x in first["candidates"]] == ["C1", "C2", "C3"]
    assert [x["candidate_id"] for x in second["candidates"]] == ["C4"]
    assert third["state"] == "WAITING_HR_SOURCING"
    assert "全部推荐完" in third["message"]


def test_unsupported_role_policy_does_not_leave_request_stuck_in_screening(tmp_path: Path):
    wf = make_workflow(tmp_path, screening=UnsupportedScreening())
    _ready(wf)
    out = wf.handle("dingtalk:alice", "开始筛选")
    assert out["state"] == "JD_READY"
    assert "当前 v5.0.2" in out["message"]
    assert "检验技术员" in out["message"]
    stored = wf.requests.latest_for_owner("dingtalk:alice")
    assert stored["status"] == "JD_READY"


def test_select_candidate_still_requires_previously_recommended_candidate(tmp_path: Path):
    wf = make_workflow(tmp_path)
    _ready(wf)
    wf.handle("dingtalk:alice", "开始筛选")
    selected = wf.handle("dingtalk:alice", "选第2个")
    assert selected["state"] == "SELECTED"
    assert "乙" in selected["message"]
