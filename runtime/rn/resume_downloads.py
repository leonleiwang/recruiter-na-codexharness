"""Expiring resume locators; every download requires the configured verified identity."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from typing import Any, Callable
from urllib.parse import urlsplit

from rn.resume_delivery import ResumeDeliveryError, read_resume_file


class DownloadDenied(Exception):
    """No candidate, identity, path or platform error is safe to disclose here."""


class TicketStore:
    def __init__(self, path: str | Path, *, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        with self._connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS resume_download_tokens (digest TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL, expires REAL NOT NULL)')
            conn.execute('CREATE INDEX IF NOT EXISTS resume_token_expiry ON resume_download_tokens(expires)')
        self.path.chmod(0o600)

    def _connect(self):
        return sqlite3.connect(self.path, timeout=15)

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def put(self, kind: str, payload: dict[str, Any], ttl: int) -> str:
        token = secrets.token_urlsafe(32)
        with self._connect() as conn:
            conn.execute('DELETE FROM resume_download_tokens WHERE expires<=?', (self.clock(),))
            conn.execute('INSERT INTO resume_download_tokens VALUES(?,?,?,?)',
                         (self._digest(token), kind, json.dumps(payload), self.clock() + ttl))
        return token

    def _read(self, token: str, kind: str, *, consume: bool) -> dict[str, Any] | None:
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
            return None
        with self._connect() as conn:
            if consume:
                conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT payload FROM resume_download_tokens WHERE digest=? AND kind=? AND expires>?',
                               (self._digest(token), kind, self.clock())).fetchone()
            if consume and row:
                conn.execute('DELETE FROM resume_download_tokens WHERE digest=?', (self._digest(token),))
        return json.loads(row[0]) if row else None

    def get(self, token: str, kind: str) -> dict[str, Any] | None:
        return self._read(token, kind, consume=False)

    def take(self, token: str, kind: str) -> dict[str, Any] | None:
        return self._read(token, kind, consume=True)


class ResumeDownloadService:
    def __init__(self, requests: Any, delivery: Any, store: TicketStore, base_url: str, *, ttl: int = 604800,
                 access_mode: str = 'owner', corp_id: str = ''):
        parsed = urlsplit(base_url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path.rstrip('/') != '/recruiter-resumes'):
            raise ValueError('RESUME_DOWNLOAD_HTTPS_BASE_REQUIRED')
        self.requests, self.delivery, self.store = requests, delivery, store
        self.base_url = base_url.rstrip('/')
        self.ttl = min(2592000, max(60, ttl))
        if access_mode not in {'owner', 'corporate'} or (access_mode == 'corporate' and not corp_id):
            raise ValueError('RESUME_DOWNLOAD_ACCESS_POLICY_REQUIRED')
        self.access_mode, self.corp_id = access_mode, corp_id
        self.audience = f'{access_mode}:{corp_id}'

    def _source(self, owner: str, request_id: str, candidate_id: str):
        request = self.requests.get_for_owner(owner, request_id)
        if not owner or not request:
            raise DownloadDenied()
        try:
            return self.delivery.prepare_recommended(request, candidate_id)
        except (ResumeDeliveryError, OSError, RuntimeError, ValueError):
            raise DownloadDenied() from None

    def url_for(self, request: dict[str, Any], candidate_id: str) -> str:
        owner, rid = str(request.get('owner_staff_id') or ''), str(request.get('request_id') or '')
        prepared = self._source(owner, rid, candidate_id)
        ticket = self.store.put('download', {'owner': owner, 'request': rid, 'candidate': candidate_id,
                                           'fingerprint': prepared['fingerprint_sha256'], 'audience': self.audience}, self.ttl)
        return self.base_url + '/d/' + ticket

    def download(self, ticket: str, authenticated_owner: str = '', *, authenticated_corp: str = '') -> tuple[str, bytes]:
        data = self.store.get(ticket, 'download')
        authorized = (authenticated_corp == self.corp_id if self.access_mode == 'corporate'
                      else bool(authenticated_owner) and data is not None and data['owner'] == authenticated_owner)
        if not data or not authorized or data.get('audience') != self.audience:
            raise DownloadDenied()
        prepared = self._source(data['owner'], data['request'], data['candidate'])
        # Open and hash the same descriptor that supplies the response. Do not
        # follow a replaced final symlink or serve bytes from a changed original.
        try:
            content, digest = read_resume_file(prepared['file_path'], self.delivery.max_bytes)
            if digest != data['fingerprint']:
                raise DownloadDenied()
        except (OSError, ResumeDeliveryError):
            raise DownloadDenied() from None
        return prepared['filename'], content


def configured_corp_id() -> str:
    explicit = os.environ.get('RECRUITER_DINGTALK_CORP_ID', '')
    if explicit:
        return explicit
    filename = os.environ.get('RECRUITER_RESUME_DOWNLOAD_TENANT_FILE', '')
    client = os.environ.get('DINGTALK_CLIENT_ID', '')
    if not filename or not client:
        return ''
    try:
        doc = json.loads(Path(filename).read_text())
        if doc['client_id_sha256'] == hashlib.sha256(client.encode()).hexdigest():
            return str(doc['corp_id'])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return ''


def from_env(requests: Any, delivery: Any) -> ResumeDownloadService | None:
    base = os.environ.get('RECRUITER_RESUME_DOWNLOAD_BASE_URL', '')
    if os.environ.get('RECRUITER_RESUME_DOWNLOAD_ENABLED') != 'true' or not base:
        return None
    path = os.environ.get('RECRUITER_RESUME_DOWNLOAD_DB')
    if not path:
        raise ValueError('RESUME_DOWNLOAD_DB_REQUIRED')
    mode = os.environ.get('RECRUITER_RESUME_DOWNLOAD_ACCESS', 'owner')
    corp = configured_corp_id() if mode == 'corporate' else ''
    if mode == 'corporate' and not corp:
        return None
    return ResumeDownloadService(requests, delivery, TicketStore(path), base, access_mode=mode, corp_id=corp)
