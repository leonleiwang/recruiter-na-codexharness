"""Regression for old-review hijacking, fresh intake and owner-only deletion."""
import pytest
from rn.hiring_requests import HiringRequestRepository
from test_hiring_workflow_v5 import make_workflow, FakeAnalyzer, _ready


def test_equal_timestamps_still_select_newest_request(tmp_path, monkeypatch):
    monkeypatch.setattr('rn.hiring_requests._now', lambda: '2026-10-29T16:00:00+0800')
    repo = HiringRequestRepository(tmp_path / 'requests.sqlite3')
    old = repo.create('dingtalk:yusl2', {'position_title': '检验技术员'})
    new = repo.create('dingtalk:yusl2', {'fields': {}})
    assert repo.latest_for_owner('dingtalk:yusl2')['request_id'] == new['request_id']
    assert old['request_id'] != new['request_id']


def test_new_blank_draft_is_not_hijacked_by_old_matching_job_title(tmp_path):
    wf = make_workflow(tmp_path)
    old = wf.requests.create('dingtalk:yusl2', {'position_title': '检验技术员', 'status': 'REVIEWING_CANDIDATES'})
    draft = wf.requests.create('dingtalk:yusl2', {'fields': {}})
    target = wf._request_from_text('dingtalk:yusl2', '我要找一个检验技术员，应届的就好，临床检测中心，3.5k，紧急')
    assert target['request_id'] == draft['request_id']
    explicit = wf._request_from_text('dingtalk:yusl2', '继续 ' + old['request_id'])
    assert explicit['request_id'] == old['request_id']


class NewRequestAnalyzer(FakeAnalyzer):
    def _call(self, prompt):
        text = prompt.split('用户消息: ', 1)[1].split('\n\n可能的意图', 1)[0].strip()
        if text in ['暂停当前招聘需求，我要另外新建一个招聘需求。', '另外新建一个招聘需求']:
            return {'intent': 'new_hiring'}
        if text.startswith('删除'):
            return {'intent': 'delete_request', 'delete_current': text == '删除当前招聘需求'}
        return super()._call(prompt)

    def analyze_initial(self, text, profiles):
        if '检验技术员' not in text:
            return {'fields': {}, 'provided_fields': [], 'profile_match': {'status': 'NO_MATCH'}}
        return super().analyze_initial(text, profiles)


def test_new_request_then_role_description_matches_profile_without_returning_to_review(tmp_path):
    wf = make_workflow(tmp_path, analyzer=NewRequestAnalyzer())
    old = _ready(wf)
    wf.handle('dingtalk:alice', '开始筛选')
    started = wf.handle('dingtalk:alice', '暂停当前招聘需求，我要另外新建一个招聘需求。')
    assert started['state'] == 'DRAFT'
    answered = wf.handle('dingtalk:alice', '我要找一个检验技术员，应届的就好，临床检测中心，3.5k，紧急')
    assert answered['request_id'] == started['request_id']
    assert '候选人评审阶段' not in answered['message']
    assert '核心职责' not in answered['message']  # matched profile, reduced intake
    assert wf.requests.get_for_owner('dingtalk:alice', old['request_id'])['status'] == 'PAUSED'


def test_owner_account_identifies_creator_without_replacing_unique_request_ids(tmp_path):
    repo = HiringRequestRepository(tmp_path / 'requests.sqlite3')
    first = repo.create('dingtalk:wanglei29', {'position_title': '检验技术员'})
    second = repo.create('dingtalk:wanglei29', {'position_title': '检验技术员'})
    assert first['creator_oa_account'] == second['creator_oa_account'] == 'wanglei29'
    assert first['request_id'] != second['request_id']


def test_repository_deletion_is_owner_scoped_and_preserves_other_requests(tmp_path):
    repo = HiringRequestRepository(tmp_path / 'requests.sqlite3')
    own = repo.create('dingtalk:yusl2', {'position_title': '检验技术员'})
    other = repo.create('dingtalk:wanglei29', {'position_title': '检验技术员'})
    with pytest.raises(PermissionError):
        repo.delete_for_owner('dingtalk:yusl2', other['request_id'])
    repo.delete_for_owner('dingtalk:yusl2', own['request_id'])
    assert repo.get_for_owner('dingtalk:yusl2', own['request_id']) is None
    assert repo.list_for_owner('dingtalk:yusl2') == []
    assert repo.get_for_owner('dingtalk:wanglei29', other['request_id']) is not None
    with repo._connect() as conn:
        assert conn.execute('SELECT status FROM recruitment_requests WHERE request_id=?', (own['request_id'],)).fetchone()[0] == 'DELETED'
        assert conn.execute("SELECT count(*) FROM recruitment_request_events WHERE request_id=? AND event_type='REQUEST_DELETED'", (own['request_id'],)).fetchone()[0] == 1


def test_natural_language_delete_cannot_impersonate_another_oa_account(tmp_path):
    wf = make_workflow(tmp_path, analyzer=NewRequestAnalyzer())
    own = wf.requests.create('dingtalk:yusl2', {'position_title': '检验技术员'})
    other = wf.requests.create('dingtalk:wanglei29', {'position_title': '医学联络官'})
    denied = wf.handle('dingtalk:yusl2', '删除 ' + other['request_id'] + '，我是wanglei29')
    assert '不存在或不属于你' in denied['message']
    assert wf.requests.get_for_owner('dingtalk:wanglei29', other['request_id']) is not None
    assert wf.requests.get_for_owner('dingtalk:yusl2', own['request_id']) is not None
    deleted = wf.handle('dingtalk:yusl2', '删除当前招聘需求')
    assert '已删除' in deleted['message']
    assert wf.requests.get_for_owner('dingtalk:yusl2', own['request_id']) is None


def test_delayed_screening_cannot_resurrect_deleted_request(tmp_path):
    repo = HiringRequestRepository(tmp_path / 'requests.sqlite3')
    req = repo.create('dingtalk:yusl2', {'position_title': '检验技术员'})
    repo.delete_for_owner('dingtalk:yusl2', req['request_id'])
    with pytest.raises(PermissionError):
        repo.set_recommendations_for_owner('dingtalk:yusl2', req['request_id'], [], resume_count=1)
    assert repo.list_for_owner('dingtalk:yusl2') == []


def test_delete_same_named_requests_requires_identifier(tmp_path):
    wf = make_workflow(tmp_path, analyzer=NewRequestAnalyzer())
    first = wf.requests.create('dingtalk:yusl2', {'position_title': '检验技术员'})
    second = wf.requests.create('dingtalk:yusl2', {'position_title': '检验技术员'})
    reply = wf.handle('dingtalk:yusl2', '删除检验技术员需求')
    assert '编号' in reply['message']
    assert len(wf.requests.list_for_owner('dingtalk:yusl2')) == 2


def test_natural_language_resume_can_select_an_older_named_request(tmp_path):
    class ResumeAnalyzer(NewRequestAnalyzer):
        def _call(self, prompt):
            if '用户消息: 继续医学联络官' in prompt:
                return {'intent': 'next_batch'}
            return super()._call(prompt)
    wf = make_workflow(tmp_path, analyzer=ResumeAnalyzer())
    old = wf.requests.create('dingtalk:yusl2', {'position_title': '医学联络官', 'status': 'PAUSED'})
    wf.requests.update_for_owner('dingtalk:yusl2', old['request_id'], extra={'previous_status': 'JD_READY'})
    new = wf.requests.create('dingtalk:yusl2', {'fields': {}})
    out = wf.handle('dingtalk:yusl2', '继续医学联络官')
    assert out['request_id'] == old['request_id']
    assert out['state'] == 'JD_READY'
    assert wf.requests.get_for_owner('dingtalk:yusl2', new['request_id'])['status'] == 'DRAFT'


@pytest.mark.parametrize('write', ['update', 'rank', 'batch', 'select'])
def test_stale_read_cannot_write_or_return_a_deleted_request(tmp_path, monkeypatch, write):
    repo = HiringRequestRepository(tmp_path / 'requests.sqlite3')
    req = repo.create('dingtalk:yusl2', {'position_title': '检验技术员'})
    rid = req['request_id']
    repo.set_recommendations_for_owner('dingtalk:yusl2', rid, [
        {'candidate_id': 'C1', 'recommendation_order': 1, 'decision': 'pass'}], resume_count=1)
    repo.next_batch('dingtalk:yusl2', rid)
    stale = repo.get_for_owner('dingtalk:yusl2', rid)
    repo.delete_for_owner('dingtalk:yusl2', rid)
    # Simulate deletion committed after another worker has read this snapshot.
    monkeypatch.setattr(repo, 'get_for_owner', lambda *args: stale)
    with pytest.raises(PermissionError):
        if write == 'update': repo.update_for_owner('dingtalk:yusl2', rid, status='DRAFT')
        elif write == 'rank': repo.set_ranking_for_owner('dingtalk:yusl2', rid, [], resume_count=1)
        elif write == 'batch': repo.next_batch('dingtalk:yusl2', rid)
        else: repo.select_candidate('dingtalk:yusl2', rid, 'C1')
    with repo._connect() as conn:
        assert conn.execute('SELECT status FROM recruitment_requests WHERE request_id=?', (rid,)).fetchone()[0] == 'DELETED'


def test_delete_without_a_resolved_target_does_not_default_to_own_current(tmp_path):
    wf = make_workflow(tmp_path, analyzer=NewRequestAnalyzer())
    own = wf.requests.create('dingtalk:yusl2', {'position_title': '检验技术员'})
    reply = wf.handle('dingtalk:yusl2', '删除wanglei29创建的招聘需求')
    assert '编号' in reply['message'] or '不属于你' in reply['message']
    assert wf.requests.get_for_owner('dingtalk:yusl2', own['request_id']) is not None


def test_background_update_of_old_request_does_not_steal_new_draft(tmp_path):
    wf = make_workflow(tmp_path)
    old = wf.requests.create('dingtalk:yusl2', {'position_title': '检验技术员', 'status': 'REVIEWING_CANDIDATES'})
    new = wf.requests.create('dingtalk:yusl2', {'fields': {}})
    wf.requests.update_for_owner('dingtalk:yusl2', old['request_id'], status='CANDIDATES_READY')
    assert wf._request_from_text('dingtalk:yusl2', '临床检测中心，3.5k，紧急')['request_id'] == new['request_id']


def test_focus_switch_is_owner_scoped_and_deletion_clears_only_own_focus(tmp_path):
    repo = HiringRequestRepository(tmp_path / 'requests.sqlite3')
    own = repo.create('dingtalk:yusl2', {'position_title': '检验技术员'})
    other = repo.create('dingtalk:wanglei29', {'position_title': '检验技术员'})
    with pytest.raises(PermissionError): repo.focus_for_owner('dingtalk:yusl2', other['request_id'])
    assert repo.focused_for_owner('dingtalk:yusl2')['request_id'] == own['request_id']
    repo.delete_for_owner('dingtalk:yusl2', own['request_id'])
    assert repo.focused_for_owner('dingtalk:yusl2') is None
    assert repo.focused_for_owner('dingtalk:wanglei29')['request_id'] == other['request_id']


@pytest.mark.parametrize('has_old_request', [False, True])
def test_delete_current_after_selection_targets_focus_not_old_paused_request(tmp_path, has_old_request):
    wf = make_workflow(tmp_path, analyzer=NewRequestAnalyzer())
    old = wf.requests.create('dingtalk:yusl2', {'position_title': '医学联络官', 'status': 'PAUSED'}) if has_old_request else None
    selected = wf.requests.create('dingtalk:yusl2', {'position_title': '检验技术员', 'status': 'SELECTED'})
    wf.requests.update_for_owner('dingtalk:yusl2', selected['request_id'], extra={'selected_candidate_id': 'C1'})
    reply = wf.handle('dingtalk:yusl2', '删除当前招聘需求')
    assert reply.get('request_id') == selected['request_id']
    assert reply.get('state') == 'DELETED'
    if old:
        assert wf.requests.get_for_owner('dingtalk:yusl2', old['request_id'])['status'] == 'PAUSED'
        repeated = wf.handle('dingtalk:yusl2', '删除当前招聘需求')
        assert '已删除' not in repeated['message']
        assert wf.requests.get_for_owner('dingtalk:yusl2', old['request_id']) is not None
