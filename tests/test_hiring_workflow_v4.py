from pathlib import Path

from rn.hiring_requests import HiringRequestRepository
from rn.hiring_workflow_v4 import HiringManagerWorkflow


class FakeProfileRepo:
    def __init__(self):
        self.profile = type("P", (), {"profile_id": "PROFILE-MSL", "name": "医学联络官", "raw": {
            "profile_id": "PROFILE-MSL", "name": "医学联络官",
            "description": "医学联络官标准画像",
            "key_abilities": [{"name": "专业影响力", "def": "与临床客户深度交流", "required": True}],
            "key_experiences": [{"name": "专业知识", "def": "临床医学或医学检验背景", "required": True}],
            "key_traits": ["责任心"],
        }})()

    def get(self, key):
        return self.profile if key == "PROFILE-MSL" else None

    def all(self):
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
            "fields": {"position_title": "医学联络官", "location": "杭州", "headcount": 1},
            "provided_fields": ["position_title", "location", "headcount"],
            "profile_match": {"status": "MATCHED", "profile_id": "PROFILE-MSL", "confidence": 0.95, "reason": "职责一致"},
        }

    def analyze_update(self, text, request, missing_fields):
        return {
            "fields": {"department": "病理科", "salary_range": "10-13K", "urgency": "尽快", "extra_hard_requirements": []},
            "provided_fields": ["department", "salary_range", "urgency", "extra_hard_requirements"],
        }


class FakeScreening:
    def rank_request(self, request):
        return {
            "resume_count": 146,
            "ranking": [
                {"candidate_id": "C1", "name": "甲", "cs": 88, "rq": 70, "jm": 96, "profile_match": 90, "jd_match": 100, "needs_verification": []},
                {"candidate_id": "C2", "name": "乙", "cs": 84, "rq": 70, "jm": 90, "profile_match": 85, "jd_match": 93, "needs_verification": []},
                {"candidate_id": "C3", "name": "丙", "cs": 80, "rq": 65, "jm": 86, "profile_match": 82, "jd_match": 89, "needs_verification": ["R01"]},
                {"candidate_id": "C4", "name": "丁", "cs": 76, "rq": 62, "jm": 82, "profile_match": 80, "jd_match": 84, "needs_verification": []},
            ],
            "trace_dir": "workspace/runs/test",
        }


def make_workflow(tmp_path: Path):
    return HiringManagerWorkflow(
        request_repo=HiringRequestRepository(tmp_path / "req.sqlite3"),
        profile_repo=FakeProfileRepo(), jd_repo=FakeJDRepo(), resume_repo=FakeResumeRepo(),
        analyzer=FakeAnalyzer(), screening_service=FakeScreening(),
    )


def test_matched_profile_only_asks_for_request_specific_missing_fields(tmp_path: Path):
    wf = make_workflow(tmp_path)
    reply = wf.handle("dingtalk:alice", "杭州招一个医学联络官，1人")
    assert reply["handled"] is True
    assert "已匹配" in reply["message"]
    assert "部门" in reply["message"]
    assert "薪资" in reply["message"]
    assert "核心职责" not in reply["message"]
    assert "学历" not in reply["message"]


def test_completed_intake_generates_jd_and_waits_for_confirmation(tmp_path: Path):
    wf = make_workflow(tmp_path)
    first = wf.handle("dingtalk:alice", "杭州招一个医学联络官，1人")
    second = wf.handle("dingtalk:alice", "病理科，10-13K，尽快，没有额外硬要求")
    assert first["request_id"] == second["request_id"]
    assert second["state"] == "JD_READY"
    assert "JD 已整理完成" in second["message"]
    assert "开始筛选" in second["message"]


def test_screen_then_next_batch_never_repeats(tmp_path: Path):
    wf = make_workflow(tmp_path)
    wf.handle("dingtalk:alice", "杭州招一个医学联络官，1人")
    wf.handle("dingtalk:alice", "病理科，10-13K，尽快，没有额外硬要求")
    first = wf.handle("dingtalk:alice", "开始筛选")
    second = wf.handle("dingtalk:alice", "换一批")
    third = wf.handle("dingtalk:alice", "再换一批")
    assert [x["candidate_id"] for x in first["candidates"]] == ["C1", "C2", "C3"]
    assert [x["candidate_id"] for x in second["candidates"]] == ["C4"]
    assert third["state"] == "WAITING_HR_SOURCING"
    assert "全部推荐完" in third["message"]


def test_progress_only_returns_current_owners_requests(tmp_path: Path):
    wf = make_workflow(tmp_path)
    wf.handle("dingtalk:alice", "杭州招一个医学联络官，1人")
    wf.handle("dingtalk:bob", "杭州招一个医学联络官，1人")
    out = wf.handle("dingtalk:alice", "查看我的招聘进展")
    assert out["handled"] is True
    assert out["request_count"] == 1
    assert "病理医生" not in out["message"]


def test_library_browsing_is_not_a_hiring_manager_capability(tmp_path: Path):
    wf = make_workflow(tmp_path)
    out = wf.handle("dingtalk:alice", "岗位画像库有多少个？")
    assert out["handled"] is True
    assert "不提供全库浏览" in out["message"]
    assert "发起招聘需求" in out["message"]

class FakeNoMatchAnalyzer(FakeAnalyzer):
    def analyze_initial(self, text, profiles):
        return {
            "fields": {"position_title": "AI Agent产品运营", "location": "杭州", "headcount": 1},
            "provided_fields": ["position_title", "location", "headcount"],
            "profile_match": {"status": "NO_MATCH", "profile_id": "", "confidence": 0.98, "reason": "14个画像均不属于该岗位"},
        }


def test_no_profile_requires_full_job_information(tmp_path: Path):
    wf = HiringManagerWorkflow(
        request_repo=HiringRequestRepository(tmp_path / "req.sqlite3"),
        profile_repo=FakeProfileRepo(), jd_repo=FakeJDRepo(), resume_repo=FakeResumeRepo(),
        analyzer=FakeNoMatchAnalyzer(), screening_service=FakeScreening(),
    )
    reply = wf.handle("dingtalk:alice", "杭州招一个AI Agent产品运营，1人")
    assert "未匹配" in reply["message"]
    assert "核心职责" in reply["message"]
    assert "关键经验/技能" in reply["message"]
    assert "硬性门槛" in reply["message"]

class FakeAmbiguousAnalyzer(FakeAnalyzer):
    def __init__(self): self.calls = 0
    def analyze_initial(self, text, profiles):
        self.calls += 1
        if self.calls == 1:
            return {
                "fields": {"position_title": "检验技术员"}, "provided_fields": ["position_title"],
                "profile_match": {"status": "AMBIGUOUS", "profile_id": "", "confidence": 0.55, "reason": "可能是社招或前处理", "candidates": ["PROFILE-A", "PROFILE-B"], "clarifying_question": "这个岗位更偏全流程检验，还是前处理？"},
            }
        return {
            "fields": {"position_title": "检验技术员"}, "provided_fields": ["position_title"],
            "profile_match": {"status": "NO_MATCH", "profile_id": "", "confidence": 0.7, "reason": "仍无法稳定匹配", "candidates": []},
        }


def test_ambiguous_profile_asks_one_targeted_question_before_full_intake(tmp_path: Path):
    analyzer = FakeAmbiguousAnalyzer()
    wf = HiringManagerWorkflow(
        request_repo=HiringRequestRepository(tmp_path / "req.sqlite3"),
        profile_repo=FakeProfileRepo(), jd_repo=FakeJDRepo(), resume_repo=FakeResumeRepo(),
        analyzer=analyzer, screening_service=FakeScreening(),
    )
    first = wf.handle("dingtalk:alice", "我要招检验技术员")
    assert first["state"] == "DRAFT"
    assert "更偏全流程检验" in first["message"]
    assert "核心职责" not in first["message"]

class FakeInvalidProfileAnalyzer(FakeAnalyzer):
    def analyze_initial(self, text, profiles):
        return {
            "fields": {"position_title": "不存在岗位"}, "provided_fields": ["position_title"],
            "profile_match": {"status": "MATCHED", "profile_id": "PROFILE-NOT-FOUND", "confidence": 0.99, "reason": "bad id"},
        }


def test_invalid_matched_profile_id_fails_safe_to_full_intake(tmp_path: Path):
    wf = HiringManagerWorkflow(
        request_repo=HiringRequestRepository(tmp_path / "req.sqlite3"),
        profile_repo=FakeProfileRepo(), jd_repo=FakeJDRepo(), resume_repo=FakeResumeRepo(),
        analyzer=FakeInvalidProfileAnalyzer(), screening_service=FakeScreening(),
    )
    reply = wf.handle("dingtalk:alice", "我要招一个不存在岗位")
    assert "核心职责" in reply["message"]
    stored = wf.requests.list_for_owner("dingtalk:alice")[0]
    assert stored["profile_match_status"] != "MATCHED"
