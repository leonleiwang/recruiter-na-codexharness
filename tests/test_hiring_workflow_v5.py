from pathlib import Path

from rn.hiring_requests import HiringRequestRepository
from rn.hiring_workflow_v5 import HiringManagerWorkflow
from rn.resume_delivery import ResumeDeliveryError
from rn.resume_screening.decision.screening_v5 import UnsupportedScreeningPolicy
from rn.resume_screening.jd.intake import HiringIntentAnalyzer


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

    def _call(self, prompt):
        message = prompt.split("用户消息: ", 1)[1].split("\n\n可能的意图", 1)[0].strip()
        if "当前需求状态: none" in prompt:
            return {"intent": "new_hiring", "selection_ordinal": None, "selection_name": ""}
        if message == "开始筛选":
            return {"intent": "start_screen", "selection_ordinal": None, "selection_name": ""}
        if message in {"换一批", "再换一批"}:
            return {"intent": "next_batch", "selection_ordinal": None, "selection_name": ""}
        if message in {"第一个", "第一位"}:
            return {"intent": "select", "selection_ordinal": 1, "selection_name": ""}
        if message in {"选第2个", "第二个", "第二位"}:
            return {"intent": "select", "selection_ordinal": 2, "selection_name": ""}
        return {"intent": "modify_jd", "selection_ordinal": None, "selection_name": ""}


class SemanticSelectionAnalyzer(FakeAnalyzer):
    def _call(self, prompt):
        if "用户消息: 第二个" in prompt:
            return {"intent": "select", "selection_ordinal": 2, "selection_name": ""}
        if "用户消息: 开始筛选" in prompt:
            return {"intent": "start_screen", "selection_ordinal": None, "selection_name": ""}
        return super()._call(prompt)


class SemanticRestartAnalyzer(FakeAnalyzer):
    def _call(self, prompt):
        if "用户消息: 重新来吧" in prompt:
            return {"intent": "restart_hiring", "selection_ordinal": None, "selection_name": ""}
        return super()._call(prompt)


class SemanticNewHiringAnalyzer(FakeAnalyzer):
    def _call(self, prompt):
        if "用户消息: 另外想新开一个医学检验岗位" in prompt:
            return {"intent": "new_hiring", "selection_ordinal": None, "selection_name": ""}
        return super()._call(prompt)


class SemanticResumeRedeliveryAnalyzer(FakeAnalyzer):
    def _call(self, prompt):
        if "用户消息: 我没看到刚才的简历，麻烦重新发个链接" in prompt:
            return {"intent": "resend_resume", "selection_ordinal": None, "selection_name": ""}
        return super()._call(prompt)


class SemanticCandidateContactAnalyzer(FakeAnalyzer):
    def _call(self, prompt):
        if "用户消息: 请云联络中心联系她" in prompt:
            return {"intent": "confirm_ccc_contact", "selection_ordinal": None, "selection_name": ""}
        if "用户消息: 这次先不要联系候选人" in prompt:
            return {"intent": "decline_ccc_contact", "selection_ordinal": None, "selection_name": ""}
        return super()._call(prompt)


class SemanticSelectionCorrectionAnalyzer(FakeAnalyzer):
    def _call(self, prompt):
        if "用户消息: 其实我想选第一个" in prompt:
            return {"intent": "select", "selection_ordinal": 1, "selection_name": ""}
        return super()._call(prompt)


class HijackAnalyzer(FakeAnalyzer):
    """复现生产误判：SELECTED 上下文里的任何消息都被读成"同意联系"。"""

    def _call(self, prompt):
        if "当前需求状态: SELECTED" in prompt:
            return {"intent": "confirm_ccc_contact", "selection_ordinal": None, "selection_name": ""}
        return super()._call(prompt)


class BrokenClassifierAnalyzer(FakeAnalyzer):
    def _call(self, prompt):
        raise RuntimeError("LLM_HTTP_500")


class PromptCapturingAnalyzer(FakeAnalyzer):
    def __init__(self):
        self.prompts = []

    def _call(self, prompt):
        self.prompts.append(prompt)
        return super()._call(prompt)


class EmptyUpdateAnalyzer(HiringIntentAnalyzer):
    """Models a valid-but-unhelpful semantic extraction response."""

    def __init__(self):
        super().__init__(base_url="test", model="test", api_key="test")

    def _call(self, prompt):
        if "用户消息:" in prompt:
            intent = "new_hiring" if "当前需求状态: none" in prompt else "modify_jd"
            return {"intent": intent, "selection_ordinal": None, "selection_name": ""}
        if "待补字段" in prompt:
            return {"fields": {"extra_hard_requirements": []}, "provided_fields": ["extra_hard_requirements"]}
        return {
            "fields": {
                "position_title": "检验技术员", "department": "实验室部", "headcount": 1,
                "location": "杭州", "salary_range": "10-13K", "urgency": "尽快",
            },
            "provided_fields": ["position_title", "department", "headcount", "location", "salary_range", "urgency"],
            "profile_match": {"status": "MATCHED", "profile_id": "PROFILE-412167F2937C", "confidence": 0.96, "reason": "职责一致"},
        }


class FakeScreening:
    def screen_request(self, request):
        return {
            "resume_count": 146,
            "recommendations": [
                {"recommendation_order": 1, "candidate_id": "C1", "name": "甲", "decision": "pass", "readiness": "ready", "recommendation_reasons": ["检验科经验：3年三甲医院检验科"], "needs_verification": [], "interview_focus": [{"item": "沟通表达能力", "reason": "面试确认"}], "preference_signals": [], "matched_tag_count": 6, "applicable_tag_count": 7, "summary": {"degree": "本科", "total_work_years": 3, "location": "杭州"}},
                {"recommendation_order": 2, "candidate_id": "C2", "name": "乙", "decision": "pass", "readiness": "ready", "recommendation_reasons": ["人员带教经验：带教2人"], "needs_verification": [], "interview_focus": [], "preference_signals": [{"item": "人员带教经验", "evidence": "带教2人"}], "summary": {"degree": "本科", "total_work_years": 4, "location": "杭州"}},
                {"recommendation_order": 3, "candidate_id": "C3", "name": "丙", "decision": "pass", "readiness": "needs_verification", "recommendation_reasons": ["检验科经验：2年"], "needs_verification": [{"item": "检验资格证", "reason": "简历未提及证书"}], "interview_focus": [], "preference_signals": [], "summary": {"degree": "大专", "total_work_years": 2, "location": "宁波"}},
                {"recommendation_order": 4, "candidate_id": "C4", "name": "丁", "decision": "pass", "readiness": "ready", "recommendation_reasons": ["检验科经验：5年"], "needs_verification": [], "interview_focus": [], "preference_signals": [], "summary": {"degree": "本科", "total_work_years": 5, "location": "杭州"}},
            ],
            "trace_dir": "workspace/runs/test-v5",
        }


class UnsupportedScreening:
    def screen_request(self, request):
        raise UnsupportedScreeningPolicy()


class BrokenScreening:
    # 模拟后端不可用（例如未配置 MySQL）；错误原文不得回给用人经理。
    def screen_request(self, request):
        raise RuntimeError("RESUME_BACKEND_UNAVAILABLE: MYSQL_DATABASE is required")


def make_workflow(tmp_path: Path, screening=None, analyzer=None):
    return HiringManagerWorkflow(
        request_repo=HiringRequestRepository(tmp_path / "req.sqlite3"),
        profile_repo=FakeProfileRepo(), jd_repo=FakeJDRepo(), resume_repo=FakeResumeRepo(),
        analyzer=analyzer or FakeAnalyzer(), screening_service=screening or FakeScreening(),
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


def test_semantic_restart_cancels_the_current_draft_and_returns_a_clean_entrypoint(tmp_path: Path):
    wf = make_workflow(tmp_path, analyzer=SemanticRestartAnalyzer())
    wf.handle("dingtalk:alice", "我要招一个检验技术员")

    out = wf.handle("dingtalk:alice", "重新来吧")
    stored = wf.requests.list_for_owner("dingtalk:alice")[0]

    assert stored["status"] == "CANCELLED"
    assert "重新开始" in out["message"]
    assert "直接描述" in out["message"]


def test_semantic_new_hiring_starts_a_separate_request_while_a_draft_exists(tmp_path: Path):
    wf = make_workflow(tmp_path, analyzer=SemanticNewHiringAnalyzer())
    first = wf.handle("dingtalk:alice", "我要招一个检验技术员")

    second = wf.handle("dingtalk:alice", "另外想新开一个医学检验岗位")

    assert second["request_id"] != first["request_id"]
    assert len(wf.requests.list_for_owner("dingtalk:alice")) == 2


def test_matched_profile_accepts_no_for_the_only_missing_list_field_when_semantic_extraction_is_empty(tmp_path: Path):
    wf = HiringManagerWorkflow(
        request_repo=HiringRequestRepository(tmp_path / "req.sqlite3"),
        profile_repo=FakeProfileRepo(), jd_repo=FakeJDRepo(), resume_repo=FakeResumeRepo(),
        analyzer=EmptyUpdateAnalyzer(), screening_service=FakeScreening(),
    )
    wf.handle("dingtalk:alice", "杭州招一个检验技术员，1人")

    out = wf.handle("dingtalk:alice", "没有")

    assert out["state"] == "JD_READY"
    assert "额外必须满足" not in out["message"]


def test_screening_renders_candidate_decisions_without_score_fields(tmp_path: Path):
    wf = make_workflow(tmp_path)
    ready = _ready(wf)
    assert ready["state"] == "JD_READY"
    out = wf.handle("dingtalk:alice", "开始筛选")
    assert [x["candidate_id"] for x in out["candidates"]] == ["C1", "C2", "C3"]
    assert "可直接推荐" in out["message"]
    assert "需确认" in out["message"]
    assert "推荐依据" in out["message"]
    assert "符合标签" in out["message"]
    assert "**通过理由：**" in out["message"]
    assert "**面试重点：**" in out["message"]
    assert "本批推荐 3 人\n\n---\n\n**1." in out["message"]
    assert "**1. 甲**｜" in out["message"]
    assert "\n\n────────────\n\n**2. 乙**｜" in out["message"]
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


def test_natural_first_ordinal_selects_the_first_candidate_in_current_batch(tmp_path: Path):
    wf = make_workflow(tmp_path)
    _ready(wf)
    wf.handle("dingtalk:alice", "开始筛选")

    selected = wf.handle("dingtalk:alice", "第一个")

    assert selected["state"] == "SELECTED"
    assert "甲" in selected["message"]


def test_semantic_selection_ordinal_selects_candidate_without_fixed_command_wording(tmp_path: Path):
    wf = make_workflow(tmp_path, analyzer=SemanticSelectionAnalyzer())
    _ready(wf)
    wf.handle("dingtalk:alice", "开始筛选")

    selected = wf.handle("dingtalk:alice", "第二个")

    assert selected["state"] == "SELECTED"
    assert "乙" in selected["message"]


def test_selected_candidate_returns_an_internal_resume_delivery_descriptor(tmp_path: Path):
    class Delivery:
        def prepare(self, request):
            assert request["selected_candidate_id"] == "C1"
            return {"candidate_id": "C1", "filename": "candidate.pdf", "file_path": "/private/candidate.pdf", "fingerprint_sha256": "a" * 64}

    wf = make_workflow(tmp_path)
    wf.delivery = Delivery()
    _ready(wf)
    wf.handle("dingtalk:alice", "开始筛选")

    selected = wf.handle("dingtalk:alice", "第一个")

    assert selected["resume_delivery"]["filename"] == "candidate.pdf"
    assert "/private" not in selected["message"]


def test_semantic_resume_redelivery_uses_the_latest_selected_request_not_a_jd_flow(tmp_path: Path):
    class Delivery:
        def prepare(self, request):
            assert request["status"] == "SELECTED"
            assert request["selected_candidate_id"] == "C1"
            return {"candidate_id": "C1", "filename": "candidate.pdf", "file_path": "/private/candidate.pdf"}

    wf = make_workflow(tmp_path, analyzer=SemanticResumeRedeliveryAnalyzer())
    wf.delivery = Delivery()
    _ready(wf)
    wf.handle("dingtalk:alice", "开始筛选")
    wf.handle("dingtalk:alice", "第一个")

    resent = wf.handle("dingtalk:alice", "我没看到刚才的简历，麻烦重新发个链接")

    assert resent["state"] == "SELECTED"
    assert resent["resume_delivery"]["candidate_id"] == "C1"
    assert "重新准备" in resent["message"]
    assert "/private" not in resent["message"]


def test_selection_does_not_solicit_an_unintegrated_contact_service(tmp_path: Path):
    """企业云联络中心外呼尚未接入，选择候选人时不得追问或承诺该服务。"""
    wf = make_workflow(tmp_path)
    _ready(wf)
    wf.handle("dingtalk:alice", "开始筛选")

    selected = wf.handle("dingtalk:alice", "第一个")

    assert selected["state"] == "SELECTED"
    assert "follow_up_message" not in selected
    assert "企业云联络中心" not in selected["message"]
    assert "语音智能体" not in selected["message"]


def test_semantic_ccc_contact_confirmation_records_intent_without_placing_a_call(tmp_path: Path):
    wf = make_workflow(tmp_path, analyzer=SemanticCandidateContactAnalyzer())
    _ready(wf)
    wf.handle("dingtalk:alice", "开始筛选")
    wf.handle("dingtalk:alice", "第一个")

    confirmed = wf.handle("dingtalk:alice", "请云联络中心联系她")
    stored = wf.requests.latest_for_owner("dingtalk:alice", statuses={"SELECTED"})

    assert confirmed["state"] == "SELECTED"
    assert "尚未接入" in confirmed["message"]
    assert "不会发起电话或录音" in confirmed["message"]
    assert "语音智能体将联系" not in confirmed["message"]
    assert stored["request"]["candidate_contact"]["status"] == "CONTACT_REQUESTED"


def test_selected_request_allows_semantic_correction_to_another_shown_candidate(tmp_path: Path):
    wf = make_workflow(tmp_path, analyzer=SemanticSelectionCorrectionAnalyzer())
    _ready(wf)
    wf.handle("dingtalk:alice", "开始筛选")
    wf.handle("dingtalk:alice", "第二个")

    corrected = wf.handle("dingtalk:alice", "其实我想选第一个")
    requests = wf.requests.list_for_owner("dingtalk:alice")

    assert corrected["state"] == "SELECTED"
    assert "已将已选候选人改为 **甲**" in corrected["message"]
    assert "follow_up_message" not in corrected
    assert len(requests) == 1
    assert requests[0]["selected_candidate_id"] == "C1"
    assert requests[0]["request"]["candidate_contact"]["status"] == "AWAITING_CONFIRMATION"


def test_field_answer_is_not_hijacked_by_an_earlier_selected_request(tmp_path: Path):
    """ISSUE-001：历史 SELECTED 需求不得接管后续消息，字段补充必须正常落库。"""
    wf = make_workflow(tmp_path, analyzer=HijackAnalyzer())
    _ready(wf)
    wf.handle("dingtalk:alice", "开始筛选")
    assert wf.handle("dingtalk:alice", "第一个")["state"] == "SELECTED"

    started = wf.handle("dingtalk:alice", "我要招一个检验技术员")
    assert started["state"] == "DRAFT"

    answered = wf.handle("dingtalk:alice", "紧急")

    assert "企业云联络中心" not in answered["message"]
    assert answered["state"] == "JD_READY"
    still_selected = [x for x in wf.requests.list_for_owner("dingtalk:alice") if x["status"] == "SELECTED"]
    assert len(still_selected) == 1
    assert still_selected[0]["request"]["candidate_contact"]["status"] == "AWAITING_CONFIRMATION"


def test_ordinal_selection_targets_the_request_that_showed_the_batch(tmp_path: Path):
    """ISSUE-002：'选第N个' 必须作用于刚展示批次的需求，而不是历史已选需求。"""
    wf = make_workflow(tmp_path, analyzer=HijackAnalyzer())
    first_ready = _ready(wf)
    wf.handle("dingtalk:alice", "开始筛选")
    wf.handle("dingtalk:alice", "第一个")

    second_start = wf.handle("dingtalk:alice", "我要招一个检验技术员")
    wf.handle("dingtalk:alice", "实验室部，10-13K，尽快，没有额外硬要求")
    wf.handle("dingtalk:alice", "开始筛选")

    picked = wf.handle("dingtalk:alice", "选第2个")

    assert "已将已选候选人改为" not in picked["message"]
    assert "乙" in picked["message"]
    first = wf.requests.get_for_owner("dingtalk:alice", first_ready["request_id"])
    second = wf.requests.get_for_owner("dingtalk:alice", second_start["request_id"])
    assert first["selected_candidate_id"] == "C1"
    assert second["selected_candidate_id"] == "C2"
    assert second["status"] == "SELECTED"


def test_intent_classifier_failure_is_reported_to_the_host_log(tmp_path: Path, capsys):
    """ISSUE-007：分类失败要留下原因，而不是只回一句"暂时无法理解"。"""
    wf = make_workflow(tmp_path)
    _ready(wf)
    wf.analyzer = BrokenClassifierAnalyzer()
    capsys.readouterr()

    out = wf.handle("dingtalk:alice", "开始筛选")

    err = capsys.readouterr().err
    assert "intent_classify_failed" in err
    assert "LLM_HTTP_500" in err
    assert out["message"] == "当前暂时无法理解这条消息，请稍后换一种说法再试。"


def test_resume_delivery_failure_logs_the_specific_reason(tmp_path: Path, capsys):
    """ISSUE-004：投递失败要记录具体原因，用户侧仍是安全文案。"""
    class FailingDelivery:
        def prepare(self, request):
            raise ResumeDeliveryError("RESUME_SOURCE_ROOTS_NOT_CONFIGURED")

    wf = make_workflow(tmp_path)
    wf.delivery = FailingDelivery()
    _ready(wf)
    wf.handle("dingtalk:alice", "开始筛选")
    capsys.readouterr()

    selected = wf.handle("dingtalk:alice", "第一个")

    err = capsys.readouterr().err
    assert "resume_delivery_failed" in err
    assert "RESUME_SOURCE_ROOTS_NOT_CONFIGURED" in err
    assert "简历原件暂未准备完成" in selected["message"]
    assert "RESUME_SOURCE" not in selected["message"]


# 补充字段被误判成"新需求"会产生重复工单；分类器必须看到当前待补字段。
def test_field_answer_prompt_carries_the_pending_intake_fields(tmp_path: Path):
    analyzer = PromptCapturingAnalyzer()
    wf = make_workflow(tmp_path, analyzer=analyzer)
    wf.handle("dingtalk:alice", "杭州招一个检验技术员，1人")
    analyzer.prompts.clear()

    wf.handle("dingtalk:alice", "实验室部，10-13K，尽快，没有额外硬要求")

    intent_prompts = [p for p in analyzer.prompts if "意图分类器" in p]
    assert intent_prompts
    latest = intent_prompts[-1]
    assert "当前待补字段: " in latest
    assert "用人部门/团队" in latest
    assert "薪资预算" in latest
    assert "期望到岗时间/紧急程度" in latest


def test_prompt_has_no_pending_fields_when_no_request_exists(tmp_path: Path):
    analyzer = PromptCapturingAnalyzer()
    wf = make_workflow(tmp_path, analyzer=analyzer)

    wf.handle("dingtalk:alice", "杭州招一个检验技术员，1人")

    intent_prompts = [p for p in analyzer.prompts if "意图分类器" in p]
    assert intent_prompts
    assert "当前待补字段: 无" in intent_prompts[0]


def test_screening_backend_failure_hides_internal_error_and_stays_retryable(tmp_path: Path, capsys):
    wf = make_workflow(tmp_path, screening=BrokenScreening())
    _ready(wf)
    capsys.readouterr()

    out = wf.handle("dingtalk:alice", "开始筛选")

    err = capsys.readouterr().err
    assert out["state"] == "JD_READY"
    assert "MYSQL_DATABASE" not in out["message"]
    assert "screening_failed" in err
    assert "MYSQL_DATABASE" in err
    assert wf.requests.latest_for_owner("dingtalk:alice")["status"] == "JD_READY"


def test_missing_labels_is_safe_for_an_incomplete_record(tmp_path: Path):
    # 记录不完整时必须返回可用文案而不是抛错，否则分类器调用会被异常打断。
    from rn.hiring_workflow_v5 import _missing_labels

    labels = _missing_labels({})

    assert isinstance(labels, str) and labels
    assert "岗位名称" in labels
