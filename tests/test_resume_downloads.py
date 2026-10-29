from copy import deepcopy
import os
from pathlib import Path

import pytest

from rn.resume_delivery import SelectedResumeDeliveryService, ResumeDeliveryError
from rn.resume_downloads import DownloadDenied, TicketStore, ResumeDownloadService


class Requests:
    def __init__(self, request):
        self.request = request

    def get_for_owner(self, owner, request_id):
        r = self.request
        return deepcopy(r) if r and r['owner_staff_id'] == owner and r['request_id'] == request_id and r['status'] != 'DELETED' else None


class Resumes:
    def __init__(self, source):
        self.source = source

    def get_candidate(self, ref):
        if ref == '42':
            return {'resume_id': 'C1', '_mysql_source_file_path': str(self.source), '_mysql_file_name': '原始简历.pdf'}


@pytest.fixture
def setup(tmp_path):
    source = tmp_path / 'original.pdf'
    source.write_bytes(b'%PDF-original')
    request = {'request_id': 'REQ1', 'owner_staff_id': 'dingtalk:alice', 'status': 'REVIEWING_CANDIDATES',
               'recommended_candidate_ids': ['C1'], 'ranking': [{'candidate_id': 'C1', 'source_resume_ref': '42'}]}
    requests = Requests(request)
    delivery = SelectedResumeDeliveryService(Resumes(source), allowed_roots=[tmp_path])
    now = [1000]
    store = TicketStore(tmp_path / 'tickets.sqlite3', clock=lambda: now[0])
    service = ResumeDownloadService(requests, delivery, store, 'https://example.test/recruiter-resumes', ttl=60)
    return source, request, requests, store, service, now


def token(service, request):
    return service.url_for(request, 'C1').rsplit('/', 1)[1]


def test_recommended_original_download_does_not_select_or_mutate_request(setup):
    source, request, requests, store, service, now = setup
    before = deepcopy(request)
    url = service.url_for(request, 'C1')
    assert url.startswith('https://example.test/recruiter-resumes/d/')
    assert len(url.rsplit('/', 1)[1]) == 43
    assert len(url.split('/')) == 6
    filename, data = service.download(url.rsplit('/', 1)[1], 'dingtalk:alice')
    assert filename == '原始简历.pdf'
    assert data == b'%PDF-original'
    assert requests.request == before


def test_forwarded_ticket_rejects_different_verified_owner(setup):
    _, request, _, _, service, _ = setup
    ticket = token(service, request)
    with pytest.raises(DownloadDenied):
        service.download(ticket, 'dingtalk:bob')


@pytest.mark.parametrize('change', ['deleted', 'unshown', 'removed', 'changed_file', 'wrong_identity'])
def test_current_permission_and_original_are_rechecked_at_download(setup, change):
    source, request, requests, store, service, now = setup
    ticket = token(service, request)
    if change == 'deleted': requests.request['status'] = 'DELETED'
    elif change == 'unshown': requests.request['recommended_candidate_ids'] = []
    elif change == 'removed': requests.request['ranking'] = []
    elif change == 'changed_file': source.write_bytes(b'%PDF-replaced')
    else: service.delivery.resume_repo.get_candidate = lambda ref: {'resume_id': 'C2', '_mysql_source_file_path': str(source)}
    with pytest.raises(DownloadDenied):
        service.download(ticket, 'dingtalk:alice')


def test_expired_ticket_never_downloads(setup):
    _, request, _, _, service, now = setup
    ticket = token(service, request)
    now[0] += 60
    with pytest.raises(DownloadDenied): service.download(ticket, 'dingtalk:alice')


def test_unshown_candidate_cannot_get_a_link(setup):
    _, request, requests, _, service, _ = setup
    requests.request['recommended_candidate_ids'] = []
    with pytest.raises(DownloadDenied): service.url_for(request, 'C1')


def test_path_outside_roots_or_missing_original_has_no_link(setup, tmp_path):
    source, request, _, _, service, _ = setup
    service.delivery.allowed_roots = [tmp_path / 'elsewhere']
    with pytest.raises(DownloadDenied): token(service, request)
    service.delivery.allowed_roots = [tmp_path]
    source.unlink()
    with pytest.raises(DownloadDenied): token(service, request)


def test_store_kinds_expiry_and_single_use(setup):
    _, _, _, store, _, now = setup
    ticket = store.put('flow', {'value': 7}, 60)
    assert store.get(ticket, 'session') is None
    assert store.take(ticket, 'flow') == {'value': 7}
    assert store.take(ticket, 'flow') is None
    ticket = store.put('session', {'owner': 'dingtalk:alice'}, 60)
    now[0] += 60
    assert store.get(ticket, 'session') is None
    assert 'dingtalk:alice' not in ticket


@pytest.mark.parametrize('base', ['http://example.test/recruiter-resumes', 'https://user:pass@example.test/recruiter-resumes', 'https://example.test/recruiter-resumes?q=1'])
def test_download_base_requires_unambiguous_https_origin(setup, base):
    _, _, requests, store, service, _ = setup
    with pytest.raises(ValueError): ResumeDownloadService(requests, service.delivery, store, base)


def test_workflow_first_next_and_redisplay_links_keep_the_batch_and_selection(tmp_path):
    from rn.hiring_workflow_v5 import HiringManagerWorkflow
    from test_hiring_workflow_v5 import make_workflow, _ready, FakeScreening, FakeAnalyzer

    class Screening(FakeScreening):
        def screen_request(self, request):
            result = super().screen_request(request)
            for i, row in enumerate(result['recommendations'], 1): row['source_resume_ref'] = str(i)
            return result

    class Analyzer(FakeAnalyzer):
        def _call(self, prompt):
            if '用户消息: 再显示本批候选人的简历链接' in prompt:
                return {'intent': 'show_current_batch'}
            return super()._call(prompt)

    class Originals:
        def get_candidate(self, ref):
            source = tmp_path / f'{ref}.pdf'
            source.write_bytes(b'%PDF-'+ref.encode())
            return {'resume_id':'C'+ref, '_mysql_source_file_path':str(source)}

    wf = make_workflow(tmp_path, screening=Screening(), analyzer=Analyzer())
    wf.downloads = ResumeDownloadService(wf.requests, SelectedResumeDeliveryService(Originals(), allowed_roots=[tmp_path]),
                                        TicketStore(tmp_path/'tickets.sqlite3'), 'https://example.test/recruiter-resumes')
    _ready(wf)
    first = wf.handle('dingtalk:alice', '开始筛选')
    assert first['message'].count('[下载个人简历](') == 3
    before = wf.requests.get_for_owner('dingtalk:alice', first['request_id'])
    refreshed = wf.handle('dingtalk:alice', '再显示本批候选人的简历链接')
    after = wf.requests.get_for_owner('dingtalk:alice', first['request_id'])
    assert [r['candidate_id'] for r in refreshed['candidates']] == ['C1','C2','C3']
    assert after == before
    assert refreshed['message'].count('[下载个人简历](') == 3
    next_batch = wf.handle('dingtalk:alice', '换一批')
    assert [r['candidate_id'] for r in next_batch['candidates']] == ['C4']
    assert next_batch['message'].count('[下载个人简历](') == 1
    ticket = next_batch['message'].split('[下载个人简历](',1)[1].split(')',1)[0].rsplit('/',1)[1]
    assert wf.downloads.download(ticket,'dingtalk:alice')[1] == b'%PDF-4'
    assert not wf.requests.get_for_owner('dingtalk:alice',first['request_id'])['selected_candidate_id']


def test_original_reader_rejects_growth_after_size_check(tmp_path, monkeypatch):
    import os
    from rn.resume_delivery import read_resume_file
    source = tmp_path / 'growing.pdf'
    source.write_bytes(b'%PDF')
    real_stat = os.fstat
    grown = [False]
    def grow_after_stat(fd):
        result = real_stat(fd)
        if not grown[0]:
            grown[0] = True
            with source.open('ab') as out: out.write(b'x'*100)
        return result
    monkeypatch.setattr(os, 'fstat', grow_after_stat)
    with pytest.raises(ResumeDeliveryError, match='TOO_LARGE'):
        read_resume_file(source, 16)


@pytest.mark.skipif(not hasattr(os, 'mkfifo'), reason='POSIX FIFO check')
def test_original_reader_never_opens_a_fifo_as_a_blocking_resume(tmp_path):
    import os
    from rn.resume_delivery import read_resume_file
    source = tmp_path / 'fifo'
    os.mkfifo(source)
    with pytest.raises(ResumeDeliveryError, match='NOT_REGULAR'):
        read_resume_file(source, 16)


def test_corporate_mode_allows_verified_same_company_but_not_anonymous_or_other_company(setup):
    _, request, requests, store, owner_service, _ = setup
    service = ResumeDownloadService(requests, owner_service.delivery, store, owner_service.base_url,
                                    access_mode='corporate', corp_id='corp-approved')
    ticket = token(service, request)
    assert service.download(ticket, '', authenticated_corp='corp-approved')[1] == b'%PDF-original'
    with pytest.raises(DownloadDenied): service.download(ticket, 'dingtalk:alice')
    with pytest.raises(DownloadDenied): service.download(ticket, '', authenticated_corp='corp-other')
    requests.request['status'] = 'DELETED'
    with pytest.raises(DownloadDenied): service.download(ticket, '', authenticated_corp='corp-approved')


def test_corporate_mode_cannot_start_without_a_fixed_corporate_identity(setup):
    _, _, requests, store, owner_service, _ = setup
    with pytest.raises(ValueError):
        ResumeDownloadService(requests, owner_service.delivery, store, owner_service.base_url, access_mode='corporate')


def test_company_change_does_not_reauthorize_old_tickets(setup):
    _, request, requests, store, old, _ = setup
    first = ResumeDownloadService(requests, old.delivery, store, old.base_url, access_mode='corporate',corp_id='corp-first')
    ticket = token(first,request)
    other = ResumeDownloadService(requests, old.delivery, store, old.base_url,access_mode='corporate',corp_id='corp-other')
    with pytest.raises(DownloadDenied):other.download(ticket,'',authenticated_corp='corp-other')
