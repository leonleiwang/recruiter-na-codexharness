"""Manager-facing labels and spacing use the exact decision tag set."""
from pathlib import Path
import pytest

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
    # This synthetic profile has two requirements; policy-only criteria must
    # never be silently added to the JD's display list.
    expected = {"规范操作与执行能力", "检验科经验"}
    assert set(saved["jd"]["screening_tag_names"]) == expected
    assert all(name in reply["message"] for name in expected)
    assert "全部匹配标签（2项）" in reply["message"]
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
    assert item["applicable_tag_count"] == 1
    assert len(item["matched_tag_names"]) == item["matched_tag_count"] == 1
    assert "沟通表达能力" not in item["matched_tag_names"]
    text = _render_candidates(request(), [item])
    assert "符合标签：1/1（检验科经验）" in text
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
    assert "全部匹配标签（2项）" in result["message"]
    assert "使用合成X平台" in result["message"] and "合成项目经历" in result["message"]
    assert "额外硬要求：" in result["message"]


def test_eligible_certificate_card_never_claims_held_certificate():
    from rn.hiring_workflow_v5 import _hard_condition_line
    text = _hard_condition_line({'details': {'certification': {'status': 'eligible_to_apply'}}})
    assert '未持证' in text and '报考资格' in text and '待核实' in text
    assert '检验资格证✅' not in text


def test_pending_exam_card_does_not_claim_held_certificate():
    from rn.hiring_workflow_v5 import _hard_condition_line
    text = _hard_condition_line({'details': {'certification': {'status': 'pending_exam'}}})
    assert '已报名待考' in text
    assert '检验资格证✅' not in text


def test_willing_location_is_visible_on_candidate_card():
    from rn.hiring_workflow_v5 import _hard_condition_line
    text = _hard_condition_line({'details': {'location': {'status': 'willing'}}})
    assert '工作地点✅（愿意到岗）' in text


def test_jd_rule_tags_drive_card_without_changing_v5_decision(tmp_path):
    from rn.resume_screening.decision.screening_v5 import evaluate_candidate_v5
    req = request()
    req['jd'] = {'title': '检验技术员', 'key_experience_skills': ['规范操作与执行能力', '检验科经验'],
                 'hard_requirements': ['必须持有检验师资格证']}
    resume = FakeResumeRepo(1).rows[0]
    resume['raw_text'] = '本科 医学检验技术 杭州 检验科3年工作经验。\n负责标本接收和上机检测，严格按照SOP规范操作。'
    # The auxiliary engine sees no certificate quote. Its qualification hint
    # must not veto v5's pending verification decision.
    class Evaluator(FakeEvaluator):
        def evaluate(self, criteria, facts):
            doc = super().evaluate(criteria, facts)
            doc['hard_checks']['certification'] = {'status': 'unknown', 'evidence': [], 'reasoning_summary': '未提供证书'}
            return doc
    result = evaluate_candidate_v5(resume, request=req, profile=FakeProfileRepo().get('PROFILE-412167F2937C'),
                                   evaluator=Evaluator(), trace_dir=tmp_path / 'candidate')
    assert result['decision'] == 'pass'
    assert any(x['item'] == '检验资格证' for x in result['needs_verification'])
    assert result['jd_tag_summary']['matched_tag_names'] == ['规范操作与执行能力', '检验科经验']
    assert result['jd_tag_summary']['matched_tag_count'] == 2
    assert result['jd_tag_summary']['applicable_tag_count'] == 2
    assert (tmp_path / 'candidate' / 'jd_tag_matching.json').is_file()


def test_recommendation_card_uses_jd_rules_not_policy_count(tmp_path):
    req = request()
    req['jd'] = {'title': '检验技术员', 'key_experience_skills': ['检验科经验']}
    screening = HiringDecisionScreeningService(resume_repo=FakeResumeRepo(4), profile_repo=FakeProfileRepo(),
        jd_repo=object(), evaluator=FakeEvaluator(), run_root=tmp_path)
    result = screening.screen_request(req)
    assert [x['candidate_id'] for x in result['rejected_candidates']] == ['C4']
    assert result['recommendations'][0]['candidate_id'] == 'C2'  # v5 order retained
    for item in result['recommendations']:
        assert item['matched_tag_names'] == ['检验科经验']
        assert item['matched_tag_count'] == item['applicable_tag_count'] == 1
    assert '符合标签：1/1（检验科经验）' in _render_candidates(req, result['recommendations'])


def test_empty_jd_does_not_fall_back_to_policy_tags(tmp_path):
    req = request()
    req['jd'] = {'title': '检验技术员', 'hard_requirements': ['大专以上学历']}
    screening = HiringDecisionScreeningService(resume_repo=FakeResumeRepo(1), profile_repo=FakeProfileRepo(),
        jd_repo=object(), evaluator=FakeEvaluator(), run_root=tmp_path)
    item = screening.screen_request(req)['recommendations'][0]
    assert item['matched_tag_names'] == []
    assert item['matched_tag_count'] == item['applicable_tag_count'] == 0
    assert '本次JD未解析出可匹配的能力/经验标签' in _render_candidates(req, [item])


@pytest.mark.parametrize('count,resume_count', [(7, 3), (8, 4), (9, 5)])
def test_jd_tag_total_tracks_resume_stage_of_actual_jd(count, resume_count, tmp_path):
    names = ['规范操作与执行能力', '问题分析与解决能力', '快速学习能力',
             '团队合作能力', '试剂耗材管理能力', '检验科经验',
             '试剂管理经验', '人员带教经验', 'PCR检测经验'][:count]
    req = request()
    req['jd'] = {'title': '检验技术员', 'key_experience_skills': names}
    screening = HiringDecisionScreeningService(resume_repo=FakeResumeRepo(1), profile_repo=FakeProfileRepo(),
        jd_repo=object(), evaluator=FakeEvaluator(), run_root=tmp_path)
    item = screening.screen_request(req)['recommendations'][0]
    assert item['applicable_tag_count'] == resume_count
    assert item['matched_tag_names'] == ['检验科经验']
    assert f'符合标签：1/{resume_count}（检验科经验）' in _render_candidates(req, [item])


def test_jd_rule_hits_do_not_require_v5_llm_tag_confirmation(tmp_path):
    class UnknownCriteria(FakeEvaluator):
        def evaluate(self, criteria, facts):
            doc = super().evaluate(criteria, facts)
            for row in doc['criteria_assessments']:
                row.update(criterion_result='unknown', evidence_status='none', evidence=[])
            return doc
    req = request()
    req['jd'] = {'title': '检验技术员', 'key_experience_skills': ['检验科经验']}
    screening = HiringDecisionScreeningService(resume_repo=FakeResumeRepo(1), profile_repo=FakeProfileRepo(),
        jd_repo=object(), evaluator=UnknownCriteria(), run_root=tmp_path)
    item = screening.screen_request(req)['recommendations'][0]
    assert item['decision'] == 'pass'
    assert item['matched_tag_names'] == ['检验科经验']
    assert item['matched_tag_count'] == item['applicable_tag_count'] == 1


def test_resume_stage_denominator_excludes_interview_only_labels(tmp_path):
    req = request()
    req['jd'] = {'title': '检验技术员', 'key_experience_skills': [
        '规范操作与执行能力', '检验科经验', '人员带教经验',
        '问题分析与解决能力', '快速学习能力', '团队合作能力']}
    class Resumes(FakeResumeRepo):
        def __init__(self):
            super().__init__(1)
            self.rows[0]['raw_text'] += '。按SOP完成标本接收和上机检测。带教2名新人。专业排名前1%。'
    service = HiringDecisionScreeningService(resume_repo=Resumes(), profile_repo=FakeProfileRepo(),
        jd_repo=object(), evaluator=FakeEvaluator(), run_root=tmp_path)
    item = service.screen_request(req)['recommendations'][0]
    assert item['matched_tag_names'] == ['规范操作与执行能力', '检验科经验', '人员带教经验']
    assert item['matched_tag_count'] == item['applicable_tag_count'] == 3
    assert '符合标签：3/3（' in _render_candidates(req, [item])


def test_missing_resume_stage_evidence_stays_in_denominator(tmp_path):
    req = request()
    req['jd'] = {'title': '检验技术员', 'key_experience_skills': [
        '规范操作与执行能力', '检验科经验', '人员带教经验', '团队合作能力']}
    service = HiringDecisionScreeningService(resume_repo=FakeResumeRepo(1), profile_repo=FakeProfileRepo(),
        jd_repo=object(), evaluator=FakeEvaluator(), run_root=tmp_path)
    item = service.screen_request(req)['recommendations'][0]
    assert item['matched_tag_count'] == 1
    assert item['applicable_tag_count'] == 3
    assert item['matched_tag_names'] == ['检验科经验']


def test_jd_summary_splits_resume_tags_from_interview_items(tmp_path):
    wf = make_workflow(tmp_path)
    _ready(wf)
    current = wf.requests.latest_for_owner('dingtalk:alice')
    current['request']['fields']['key_experience_skills'] = ['团队合作能力', '问题分析与解决能力']
    wf.requests.update_for_owner('dingtalk:alice', current['request_id'], request=current['request'])
    reply = wf._complete_intake('dingtalk:alice', current)
    saved = wf.requests.latest_for_owner('dingtalk:alice')['jd']
    assert saved['screening_tag_names'] == ['规范操作与执行能力', '检验科经验']
    assert saved['interview_tag_names'] == ['团队合作能力', '问题分析与解决能力']
    assert '面试确认项（2项）' in reply['message']


def test_interview_focus_shows_question_direction_then_label():
    item = {'candidate_id': 'synthetic', 'interview_focus': [
        {'item': '问题分析与解决能力', 'reason': '简历未提及相关经历',
         'question_direction': '异常处理；根因排查与对策'},
        {'item': '快速学习能力', 'reason': '简历未体现学习描述',
         'question_direction': '新流程或仪器的学习与独立上手'}]}
    text = _render_candidates(request(), [item])
    assert '① 异常处理；根因排查与对策（问题分析与解决能力）' in text
    assert '② 新流程或仪器的学习与独立上手（快速学习能力）' in text
    assert '简历未提及' not in text and '简历未体现' not in text


def test_old_interview_focus_drops_repetitive_missing_evidence_reasons():
    text = _render_candidates(request(), [{'candidate_id': 'old', 'interview_focus': [
        {'item': '团队合作能力', 'reason': '简历未提及协作经历'}]}])
    assert '围绕实际经历提问（团队合作能力）' in text
    assert '简历未提及' not in text


def test_screening_supplies_interview_directions_from_approved_criteria(tmp_path):
    service = HiringDecisionScreeningService(resume_repo=FakeResumeRepo(1), profile_repo=FakeProfileRepo(),
        jd_repo=object(), evaluator=FakeEvaluator(), run_root=tmp_path)
    item = service.screen_request(request())['recommendations'][0]
    focus = next(row for row in item['interview_focus'] if row['item'] == '问题分析与解决能力')
    assert focus['question_direction'] == '快速辨识问题根因，制定对策，迅速响应突发负面状况'
    text = _render_candidates(request(), [item])
    assert '快速辨识问题根因，制定对策，迅速响应突发负面状况（问题分析与解决能力）' in text
