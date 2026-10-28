"""Approved business boundary: graduate exam eligibility is not a held certificate."""
import copy
import json
from types import SimpleNamespace

import pytest

from rn.resume_screening.decision.criteria_adapter_v5 import build_actual_criteria
from rn.resume_screening.decision.candidate_decision_v5 import decide_candidate
from rn.resume_screening.decision.decision_evidence_evaluator import DecisionEvidenceEvaluator, _build_prompt
from rn.resume_screening.decision.evidence_grounding import repair_decision_grounding

GRADUATE = 'PROFILE-91603AA55F6E'
SOCIAL = 'PROFILE-412167F2937C'
QUOTE = '具备检验士报考资格，尚未报名未持证'
FACTS = {'candidate_id': 'SYNTHETIC', 'raw_text': '本科医学检验技术。杭州。' + QUOTE}


def criteria(profile_id):
    req = {'request_id': 'SYNTHETIC', 'request': {'fields': {'position_title': '检验技术员', 'location': '杭州'},
           'profile_match': {'status': 'MATCHED', 'profile_id': profile_id}}}
    return build_actual_criteria(req, SimpleNamespace(profile_id=profile_id, name='检验技术员'))


def assessment(c, status='eligible_to_apply', evidence=True):
    return {'schema_version': '5.0', 'candidate_id': 'SYNTHETIC', 'policy_id': c['policy_id'], 'evaluation_status': 'COMPLETE',
            'hard_checks': {
                'education': {'status': 'matched', 'evidence': [{'text': '本科医学检验技术', 'source_path': 'raw_text'}]},
                'location': {'status': 'match', 'evidence': [{'text': '杭州', 'source_path': 'raw_text'}]},
                'certification': {'status': status, 'evidence': [{'text': QUOTE, 'source_path': 'raw_text'}] if evidence else []}},
            'red_line_assessments': [{'red_line_id': f'RL{i+1:02d}', 'status': 'unknown', 'evidence': []} for i, _ in enumerate(c['hard_filters']['red_lines'])],
            'criteria_assessments': [{'criterion_id': row['criterion_id'], 'criterion_result': 'unknown', 'evidence_status': 'none', 'evidence': []} for row in c['criteria']]}


def test_only_approved_graduate_profile_accepts_exam_eligibility():
    graduate, social = criteria(GRADUATE), criteria(SOCIAL)
    assert 'eligible_to_apply' in graduate['hard_filters']['certification_accepted_statuses']
    assert 'eligible_to_apply' not in social['hard_filters'].get('certification_accepted_statuses', [])
    assert '报考资格' in graduate['hard_filters']['certification']
    assert '已报名待考' in social['hard_filters']['certification']  # preserve existing non-graduate text


def test_graduate_eligible_not_registered_can_be_recommended_without_claiming_certificate():
    c = criteria(GRADUATE)
    result = decide_candidate(c, assessment(c), FACTS)
    assert result['decision'] == 'pass'
    assert result['hard_check_summary']['details']['certification']['status'] == 'eligible_to_apply'
    assert result['matched_tag_count'] == 3  # education + location + approved eligibility
    assert any('报考资格' in row['reason'] and '未持证' in row['reason'] for row in result['needs_verification'])


def test_same_eligible_not_certified_facts_do_not_relax_social_hiring():
    c = criteria(SOCIAL)
    result = decide_candidate(c, assessment(c), FACTS)
    assert result['decision'] == 'fail'
    assert any(row['criterion'] == '检验资格证' for row in result['fail_reasons'])


@pytest.mark.parametrize('status', ['valid', 'pending_exam', 'unknown', 'expired', 'none'])
def test_existing_certificate_statuses_keep_their_behavior(status):
    c = criteria(GRADUATE)
    result = decide_candidate(c, assessment(c, status=status), FACTS)
    assert (result['decision'] == 'fail') == (status in {'expired', 'none'})


def test_actual_evaluator_contract_and_grounding_accept_new_status_with_quote():
    c = criteria(GRADUATE)
    doc = assessment(c)
    evaluator = DecisionEvidenceEvaluator(base_url='https://synthetic.invalid/v1', model='synthetic', api_key='synthetic',
         post_json=lambda *args, **kwargs: {'output_text': json.dumps(doc)}, consistency='off')
    result = evaluator.evaluate(c, FACTS)
    repaired, _ = repair_decision_grounding(FACTS, result)
    assert repaired['hard_checks']['certification']['status'] == 'eligible_to_apply'
    assert decide_candidate(c, repaired, FACTS)['decision'] == 'pass'
    prompt = _build_prompt(c, FACTS)
    assert 'eligible_to_apply' in prompt
    assert '不得仅凭学历' in prompt


def test_unquoted_eligibility_downgrades_to_unknown_not_veto_or_confirmed_tag():
    c = criteria(GRADUATE)
    doc = assessment(c, evidence=False)
    evaluator = DecisionEvidenceEvaluator(base_url='https://synthetic.invalid/v1', model='synthetic', api_key='synthetic',
         post_json=lambda *args, **kwargs: {'output_text': json.dumps(doc)}, consistency='off')
    result = evaluator.evaluate(c, FACTS)
    assert result['hard_checks']['certification']['status'] == 'unknown'
    decision = decide_candidate(c, result, FACTS)
    assert decision['decision'] == 'pass'
    assert decision['matched_tag_count'] == 2


def test_fabricated_eligibility_quote_cannot_be_grounded():
    c = criteria(GRADUATE)
    doc = assessment(c)
    doc['hard_checks']['certification']['evidence'][0]['text'] = 'not in the synthetic facts'
    repaired, _ = repair_decision_grounding(FACTS, doc)
    assert repaired['hard_checks']['certification']['status'] == 'unknown'


def test_social_reagent_experience_remains_preference():
    c = criteria(SOCIAL)
    row = next(row for row in c['criteria'] if row['name'] == '试剂管理经验')
    assert row['level'] == '优先' and row['decision_mode'] == 'preference'


@pytest.mark.parametrize('status, resolved', [('AMBIGUOUS', False), ('NO_MATCH', False), ('MATCHED', False), ('AMBIGUOUS', True), ('NO_MATCH', True)])
def test_unconfirmed_or_unresolved_profile_cannot_grant_graduate_exception(status, resolved):
    req = {'request_id': 'SYNTHETIC', 'request': {'fields': {'position_title': '检验技术员'},
           'profile_match': {'status': status, 'profile_id': GRADUATE}}}
    profile = SimpleNamespace(profile_id=GRADUATE, name='检验技术员') if resolved else None
    c = build_actual_criteria(req, profile)
    assert 'eligible_to_apply' not in c['hard_filters'].get('certification_accepted_statuses', [])
    assert decide_candidate(c, assessment(c), FACTS)['decision'] == 'fail'
