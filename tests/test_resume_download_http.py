import http.client
import json
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlsplit

import pytest

from rn.resume_download_http import DownloadHTTPServer, DingTalkIdentity, IdentityUnavailable
from rn.resume_downloads import TicketStore, ResumeDownloadService
from rn.resume_delivery import SelectedResumeDeliveryService
from test_resume_downloads import Requests, Resumes


class Identity:
    client_id = 'public-client'
    scope = 'openid'
    def owner_for_code(self, code):
        if code != 'valid': raise IdentityUnavailable()
        return 'dingtalk:alice'
    def identity_for_code(self, code):
        return {'owner': self.owner_for_code(code)}


@pytest.fixture
def server(tmp_path):
    source = tmp_path / 'source.pdf'; source.write_bytes(b'%PDF-original')
    request = {'request_id': 'R1', 'owner_staff_id': 'dingtalk:alice', 'status': 'REVIEWING_CANDIDATES',
               'ranking': [{'candidate_id': 'C1', 'source_resume_ref': '42'}], 'recommended_candidate_ids': ['C1']}
    service = ResumeDownloadService(Requests(request), SelectedResumeDeliveryService(Resumes(source), allowed_roots=[tmp_path]), TicketStore(tmp_path/'tokens.sqlite3'), 'https://example.test/recruiter-resumes')
    http = DownloadHTTPServer(('127.0.0.1', 0), service, Identity())
    thread = Thread(target=http.serve_forever, daemon=True); thread.start()
    yield http, service, request
    http.shutdown(); http.server_close(); thread.join()


def get(server, path, cookie=''):
    connection = http.client.HTTPConnection(*server.server_address)
    connection.request('GET', path, headers={'Cookie': cookie})
    response = connection.getresponse(); body = response.read()
    result = response.status, dict(response.getheaders()), body
    connection.close()
    return result


def test_browser_login_is_required_and_download_has_attachment_headers(server):
    http, service, request = server
    path = urlsplit(service.url_for(request, 'C1')).path
    status, headers, _ = get(http, path)
    assert status == 302
    auth = urlsplit(headers['Location'])
    assert auth.scheme == 'https' and auth.netloc == 'login.dingtalk.com'
    params = parse_qs(auth.query)
    assert params['redirect_uri'] == ['https://example.test/recruiter-resumes/callback']
    assert params['scope'] == ['openid']
    cookie = headers['Set-Cookie'].split(';', 1)[0]
    callback = '/recruiter-resumes/callback?state=' + params['state'][0] + '&authCode=valid'
    status, headers, body = get(http, callback, cookie)
    assert status == 303 and headers['Location'] == path
    assert all(flag in headers['Set-Cookie'] for flag in ('Secure', 'HttpOnly', 'SameSite=Lax'))
    session = headers['Set-Cookie'].split(';', 1)[0]
    assert get(http, callback, cookie)[0] == 403  # one-time state
    status, headers, body = get(http, path, session)
    assert status == 200 and body == b'%PDF-original'
    assert headers['Content-Disposition'].startswith('attachment;')
    assert "filename*=UTF-8''" in headers['Content-Disposition']
    assert headers['Cache-Control'] == 'no-store'
    assert headers['Referrer-Policy'] == 'no-referrer'
    assert request['status'] == 'REVIEWING_CANDIDATES'


def test_login_state_cannot_be_used_from_another_browser(server):
    http, service, request = server
    path = urlsplit(service.url_for(request, 'C1')).path
    _, headers, _ = get(http, path)
    state = parse_qs(urlsplit(headers['Location']).query)['state'][0]
    callback = '/recruiter-resumes/callback?state='+state+'&authCode=valid'
    assert get(http, callback)[0] == 403
    assert get(http, path, '__Secure-rn-download=forged')[0] == 302


def test_wrong_authenticated_owner_cannot_download_or_learn_paths(server):
    http, service, request = server
    path = urlsplit(service.url_for(request, 'C1')).path
    session = service.store.put('session', {'owner': 'dingtalk:bob'}, 60)
    status, _, body = get(http, path, '__Secure-rn-download='+session)
    assert status == 403
    assert b'alice' not in body and b'source.pdf' not in body


def test_invalid_or_expired_link_does_not_start_oauth(server):
    http, _, _ = server
    status, headers, body = get(http, '/recruiter-resumes/d/invalid')
    assert status == 404 and 'Location' not in headers


def test_deleted_request_revokes_logged_in_download(server):
    http, service, request = server
    path = urlsplit(service.url_for(request, 'C1')).path
    session = service.store.put('session', {'owner': 'dingtalk:alice'}, 60)
    service.requests.request['status'] = 'DELETED'
    assert get(http, path, '__Secure-rn-download='+session)[0] == 403


class Response:
    def __init__(self, doc): self.doc = doc
    def raise_for_status(self): pass
    def json(self): return self.doc


class Transport:
    def __init__(self, external=False): self.calls=[]; self.external=external
    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url.endswith('/userAccessToken'): return Response({'accessToken': 'user-secret'})
        if url.endswith('/accessToken'): return Response({'accessToken': 'app-secret-token'})
        return Response({'errcode':0, 'result': {'userid':'staff-id', 'contact_type': 1 if self.external else 0}})
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return Response({'unionId':'verified-union'})


def test_identity_is_resolved_from_code_and_corporate_mapping():
    transport = Transport()
    identity = DingTalkIdentity('client-id','client-secret', transport=transport)
    assert identity.owner_for_code('browser-code') == 'dingtalk:staff-id'
    assert transport.calls[0][1]['json']['code'] == 'browser-code'
    assert transport.calls[1][1]['headers']['x-acs-dingtalk-access-token'] == 'user-secret'
    assert transport.calls[-1][1]['json'] == {'unionid':'verified-union'}
    assert 'client-secret' not in transport.calls[0][0]


def test_external_contact_identity_is_not_a_staff_owner():
    with pytest.raises(IdentityUnavailable):
        DingTalkIdentity('client-id','client-secret', transport=Transport(external=True)).owner_for_code('code')


def test_corporate_identity_uses_only_oauth_code_and_verified_enterprise_claim():
    from rn.resume_download_http import DingTalkCorporateIdentity
    class CorporateTransport:
        def post(self, url, **kwargs):
            assert url == 'https://api.dingtalk.com/v1.0/oauth2/userAccessToken'
            assert kwargs['json']['code'] == 'authorized-code'
            return Response({'accessToken':'user-secret', 'corpId':'corp-approved'})
    identity = DingTalkCorporateIdentity('client','secret','corp-approved',transport=CorporateTransport())
    assert identity.identity_for_code('authorized-code') == {'corp_id':'corp-approved'}
    assert identity.scope == 'openid corpid'
    with pytest.raises(IdentityUnavailable):
        DingTalkCorporateIdentity('client','secret','corp-other',transport=CorporateTransport()).identity_for_code('authorized-code')


def test_corporate_oauth_rejects_absent_company_claim():
    from rn.resume_download_http import DingTalkCorporateIdentity
    with pytest.raises(IdentityUnavailable):
        DingTalkCorporateIdentity('client','secret','corp-approved',transport=Transport()).identity_for_code('code')


def test_corporate_browser_flow_needs_no_member_lookup_and_uses_company_session(server):
    from rn.resume_download_http import DingTalkCorporateIdentity
    http, old, request = server
    class CorporateTransport:
        def post(self, url, **kwargs):
            assert url.endswith('/oauth2/userAccessToken')
            return Response({'accessToken':'temporary-user-token','corpId':'corp-approved'})
    http.service = ResumeDownloadService(old.requests,old.delivery,old.store,old.base_url, access_mode='corporate',corp_id='corp-approved')
    http.identity = DingTalkCorporateIdentity('client','secret','corp-approved',transport=CorporateTransport())
    path = urlsplit(http.service.url_for(request,'C1')).path
    _, headers, _ = get(http,path)
    query = parse_qs(urlsplit(headers['Location']).query)
    assert query['scope']==['openid corpid'] and query['corpId']==['corp-approved']
    cookie = headers['Set-Cookie'].split(';',1)[0]
    status, headers, _ = get(http,'/recruiter-resumes/callback?authCode=valid&state='+query['state'][0],cookie)
    assert status==303
    session=headers['Set-Cookie'].split(';',1)[0]
    assert get(http,path,session)[2]==b'%PDF-original'


def test_link_mode_downloads_without_oauth_and_cannot_use_owner_mode_ticket(server):
    http, service, request = server
    owner_path = urlsplit(service.url_for(request, 'C1')).path
    link_service = ResumeDownloadService(service.requests, service.delivery, service.store, service.base_url, access_mode='link')
    http.service = link_service
    http.identity = None
    path = urlsplit(link_service.url_for(request, 'C1')).path
    status, headers, body = get(http, path)
    assert status == 200 and body == b'%PDF-original'
    assert 'Location' not in headers and 'Set-Cookie' not in headers
    assert headers['Content-Disposition'].startswith('attachment;')
    assert get(http, owner_path)[0] == 403
    assert get(http, path[:-1] + ('A' if path[-1] != 'A' else 'B'))[0] == 404
    service.requests.request['status'] = 'DELETED'
    assert get(http, path)[0] == 403
