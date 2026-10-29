"""Manager-facing labels and spacing use the exact decision tag set."""
from pathlib import Path

from rn.hiring_workflow_v5 import _render_candidates
from rn.resume_screening.decision.candidate_decision_v5 import decide_candidate
from rn.resume_screening.decision.screening_v5 import HiringDecisionScreeningService
from test_candidate_decision_v5 import CRITERIA, assessment
from test_hiring_workflow_v5 import make_workflow, _ready
from test_screening_v5 import FakeEvaluator, FakeProfileRepo, FakeResumeRepo, request


def test_hit_names_are_exactly_the_counted_confirmed_labels():
    result = decide_candidate(CRITERIA, assessment(c3="satisfied"))
    assert result["matched_tag_names"] == ["检验科经验", "沟通表达能力"]
    assert result["matched_tag_count"] == 2
    assert result["applicable_tag_count"] == 3
    assert "学历" not in result["matched_tag_names"]
    assert "检验资格证" not in result["matched_tag_names"]
    assert "工作地点" not in result["matched_tag_names"]
    assert result["decision"] == "pass"


def test_all_request_labels_are_capability_and_experience_only(tmp_path):
    wf = make_workflow(tmp_path)
    reply = _ready(wf)
    saved = wf.requests.latest_for_owner("dingtalk:alice")
    expected = {"规范操作与执行能力", "问题分析与解决能力", "快速学习能力", "沟通表达能力",
                "团队合作能力", "试剂耗材管理能力", "检验科经验", "试剂管理经验", "人员带教经验"}
    assert set(saved["jd"]["screening_tag_names"]) == expected
    assert all(name in reply["message"] for name in expected)
    assert "全部匹配标签（9项）" in reply["message"]
    for excluded in ("学历", "检验资格证", "工作地点", "细心", "抗压", "责任心", "积极主动", "持续学习"):
        assert excluded not in saved["jd"]["screening_tag_names"]
    assert "\n\n**" in reply["message"]
    assert "语义评分" not in reply["message"]
    for internal in ["decision_mode", "hard_gate", "interview_verify", "criterion_id"]:
        assert internal not in reply["message"]


def test_names_survive_screening_and_show_inside_parentheses(tmp_path: Path):
    screening = HiringDecisionScreeningService(resume_repo=FakeResumeRepo(2), profile_repo=FakeProfileRepo(),
        jd_repo=object(), evaluator=FakeEvaluator(), run_root=tmp_path)
    result = screening.screen_request(request())
    item = result["recommendations"][0]
    assert item["applicable_tag_count"] == 9
    assert len(item["matched_tag_names"]) == item["matched_tag_count"] == 3
    assert "沟通表达能力" not in item["matched_tag_names"]
    text = _render_candidates(request(), [item])
    assert "符合标签：3/9（" + "、".join(item["matched_tag_names"]) + "）" in text
    # 资格门槛单独一行，不混进「符合标签」。
    assert "**硬性条件：** 学历✅、检验资格证✅、工作地点✅" in text


def test_candidate_blocks_have_visible_separator_and_blank_lines():
    rows = [{"candidate_id":str(i),"name":f"合成{i}","applicable_tag_count":9,"matched_tag_count":0,
             "matched_tag_names":[]} for i in range(3)]
    text = _render_candidates(request(), rows)
    assert text.count("\n\n────────────\n\n") == 2
    assert "符合标签：0/9（暂无已确认命中标签）" in text


def test_legacy_count_does_not_invent_unrecorded_tag_names():
    text = _render_candidates(request(), [{"candidate_id":"old","matched_tag_count":6,"applicable_tag_count":16}])
    assert "符合标签：6/16（历史记录未保存标签明细）" in text


def test_explicit_extra_requirements_are_shown_as_hard_requirements(tmp_path):
    wf = make_workflow(tmp_path)
    _ready(wf)
    current = wf.requests.latest_for_owner("dingtalk:alice")
    current["request"]["fields"]["extra_hard_requirements"] = ["使用合成X平台", "合成项目经历"]
    wf.requests.update_for_owner("dingtalk:alice", current["request_id"], request=current["request"])
    result = wf._complete_intake("dingtalk:alice", current)
    # 额外硬要求是门槛，不是能力/经验标签，不进「匹配标签」清单。
    assert "全部匹配标签（9项）" in result["message"]
    assert "使用合成X平台" in result["message"] and "合成项目经历" in result["message"]
    assert "额外硬要求：" in result["message"]
