"""Loopback-only original-resume gateway with DingTalk corporate identity."""
from __future__ import annotations

import hashlib
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
import secrets
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from typing import Any

import requests

from rn.resume_downloads import DownloadDenied, ResumeDownloadService, from_env

PREFIX = '/recruiter-resumes'
SESSION_COOKIE = '__Secure-rn-download'
FLOW_COOKIE = '__Secure-rn-download-flow'


class IdentityUnavailable(Exception):
    pass


class DingTalkIdentity:
    scope = 'openid'
    def __init__(self, client_id: str, client_secret: str, *, transport: Any = requests):
        if not client_id or not client_secret:
            raise ValueError('DINGTALK_IDENTITY_NOT_CONFIGURED')
        self.client_id, self.client_secret, self.transport = client_id, client_secret, transport

    def _call(self, method: str, url: str, **kwargs):
        response = getattr(self.transport, method)(url, timeout=12, allow_redirects=False, **kwargs)
        response.raise_for_status()
        doc = response.json()
        if not isinstance(doc, dict) or doc.get('errcode', 0) != 0 or doc.get('code'):
            raise IdentityUnavailable()
        return doc

    def owner_for_code(self, code: str) -> str:
        try:
            user = self._call('post', 'https://api.dingtalk.com/v1.0/oauth2/userAccessToken', json={
                'clientId': self.client_id, 'clientSecret': self.client_secret,
                'code': code, 'grantType': 'authorization_code'})
            info = self._call('get', 'https://api.dingtalk.com/v1.0/contact/users/me',
                              headers={'x-acs-dingtalk-access-token': user['accessToken']})
            union = info['unionId']
            if not isinstance(union, str) or not union:
                raise IdentityUnavailable()
            app = self._call('post', 'https://api.dingtalk.com/v1.0/oauth2/accessToken',
                             json={'appKey': self.client_id, 'appSecret': self.client_secret})
            mapped = self._call('post', 'https://oapi.dingtalk.com/topapi/user/getbyunionid',
                                params={'access_token': app['accessToken']}, json={'unionid': union})['result']
            staff = mapped['userid']
            if mapped.get('contact_type', 0) != 0 or not isinstance(staff, str) or not re.fullmatch(r'[\w.@-]{1,128}', staff):
                raise IdentityUnavailable()
            # Matches the trusted Stream adapter's normalized senderStaffId.
            return 'dingtalk:' + staff
        except Exception:
            # Provider exceptions may contain secrets, codes or personal data.
            raise IdentityUnavailable() from None

    def identity_for_code(self, code: str) -> dict[str, str]:
        return {'owner': self.owner_for_code(code)}


class DingTalkCorporateIdentity(DingTalkIdentity):
    scope = 'openid corpid'

    def __init__(self, client_id: str, client_secret: str, corp_id: str, **kwargs):
        super().__init__(client_id, client_secret, **kwargs)
        if not corp_id:
            raise ValueError('DINGTALK_CORPORATE_ID_REQUIRED')
        self.corp_id = corp_id

    def identity_for_code(self, code: str) -> dict[str, str]:
        try:
            user = self._call('post', 'https://api.dingtalk.com/v1.0/oauth2/userAccessToken', json={
                'clientId': self.client_id, 'clientSecret': self.client_secret,
                'code': code, 'grantType': 'authorization_code'})
            if not user.get('accessToken') or user.get('corpId') != self.corp_id:
                raise IdentityUnavailable()
            return {'corp_id': self.corp_id}
        except Exception:
            raise IdentityUnavailable() from None


class DownloadHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, service: ResumeDownloadService, identity: DingTalkIdentity | None):
        if address[0] != '127.0.0.1':
            raise ValueError('RESUME_DOWNLOAD_LOOPBACK_ONLY')
        self.service, self.identity = service, identity
        super().__init__(address, DownloadHandler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(15)
        return connection, address


class DownloadHandler(BaseHTTPRequestHandler):
    server: DownloadHTTPServer
    server_version = 'RecruiterNa'
    sys_version = ''

    def log_message(self, format, *args):
        # Request paths contain opaque locators or OAuth codes. Never log them.
        pass

    def _reply(self, status: int, content: str | bytes = '', **headers):
        body = content.encode('utf-8') if isinstance(content, str) else content
        self.send_response(status)
        for name, value in {
            'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'no-store',
            'Referrer-Policy': 'no-referrer', 'X-Content-Type-Options': 'nosniff',
            'Content-Security-Policy': "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
            **headers,
        }.items():
            self.send_header(name, value)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _cookie(name, value, ttl):
        return f'{name}={value}; Path={PREFIX}; Max-Age={ttl}; Secure; HttpOnly; SameSite=Lax'

    def do_GET(self):
        try:
            self._get()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self._reply(503, '下载服务暂不可用，请稍后重试或联系 HR。')

    def _get(self):
        if len(self.path) > 4096:
            return self._reply(400)
        path = urlsplit(self.path)
        if path.path == PREFIX + '/health' and not path.query:
            return self._reply(200, '{"ok":true}')
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
        except Exception:
            return self._reply(403, '身份校验失败。')
        store = self.server.service.store
        if path.path == PREFIX + '/callback':
            if self.server.service.access_mode == 'link':
                return self._reply(404)
            query = parse_qs(path.query, max_num_fields=10)
            if len(query.get('state', [])) != 1 or len(query.get('authCode', [])) != 1:
                return self._reply(403, '身份校验失败，请重新打开简历链接。')
            state = query['state'][0]
            flow = store.get(state, 'flow')
            browser = cookie[FLOW_COOKIE].value if FLOW_COOKIE in cookie else ''
            if not flow or not browser or not secrets.compare_digest(flow['browser_hash'], hashlib.sha256(browser.encode()).hexdigest()):
                return self._reply(403, '身份校验失败，请重新打开简历链接。')
            flow = store.take(state, 'flow')
            if not flow or not store.get(flow['ticket'], 'download'):
                return self._reply(403, '链接已失效，请在招聘对话中重新获取。')
            try:
                principal = self.server.identity.identity_for_code(query['authCode'][0])
            except IdentityUnavailable:
                return self._reply(503, '钉钉身份校验暂不可用，请联系管理员。')
            session = store.put('session', principal, 900)
            return self._reply(303, Location=PREFIX+'/d/'+flow['ticket'],
                               **{'Set-Cookie': self._cookie(SESSION_COOKIE, session, 900)})
        match = re.fullmatch(re.escape(PREFIX) + r'/d/([A-Za-z0-9_-]{43})', path.path)
        if not match or path.query:
            return self._reply(404, '简历链接无效或已失效，请在招聘对话中重新获取。')
        ticket = match[1]
        if not store.get(ticket, 'download'):
            return self._reply(404, '简历链接无效或已失效，请在招聘对话中重新获取。')
        if self.server.service.access_mode == 'link':
            return self._attachment(ticket)
        session = store.get(cookie[SESSION_COOKIE].value, 'session') if SESSION_COOKIE in cookie else None
        if not session:
            browser = secrets.token_urlsafe(32)
            state = store.put('flow', {'ticket': ticket, 'browser_hash': hashlib.sha256(browser.encode()).hexdigest()}, 600)
            authorize = 'https://login.dingtalk.com/oauth2/auth?' + urlencode({
                'redirect_uri': self.server.service.base_url+'/callback',
                'response_type': 'code', 'client_id': self.server.identity.client_id,
                'scope': self.server.identity.scope, 'prompt': 'consent', 'state': state,
                **({'corpId': self.server.identity.corp_id} if hasattr(self.server.identity, 'corp_id') else {})})
            return self._reply(302, Location=authorize, **{'Set-Cookie': self._cookie(FLOW_COOKIE, browser, 600)})
        return self._attachment(ticket, session)

    def _attachment(self, ticket, session=None):
        session = session or {}
        try:
            filename, content = self.server.service.download(ticket, session.get('owner', ''), authenticated_corp=session.get('corp_id', ''))
        except DownloadDenied:
            return self._reply(403, '无权下载该简历，或该需求、原件已经失效。')
        disposition = "attachment; filename=resume; filename*=UTF-8''" + quote(filename, safe='')
        return self._reply(200, content, **{'Content-Type': 'application/octet-stream', 'Content-Disposition': disposition})


def main():
    from rn.hiring_requests import HiringRequestRepository
    from rn.repositories import ResumeRepository
    from rn.resume_delivery import SelectedResumeDeliveryService
    service = from_env(HiringRequestRepository(), SelectedResumeDeliveryService(ResumeRepository()))
    if service is None:
        raise SystemExit('RESUME_DOWNLOAD_NOT_CONFIGURED')
    credentials = (os.environ.get('DINGTALK_CLIENT_ID', ''), os.environ.get('DINGTALK_CLIENT_SECRET', ''))
    identity = (None if service.access_mode == 'link' else DingTalkCorporateIdentity(*credentials, service.corp_id) if service.access_mode == 'corporate'
                else DingTalkIdentity(*credentials))
    server = DownloadHTTPServer(('127.0.0.1', int(os.environ.get('RECRUITER_RESUME_DOWNLOAD_PORT', '18891'))), service, identity)
    server.serve_forever()


if __name__ == '__main__':
    main()
