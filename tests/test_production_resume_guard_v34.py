from __future__ import annotations

from pathlib import Path

import pytest

from rn.repositories.resume_repository import ResumeRepository

ROOT = Path(__file__).resolve().parents[1]


def test_production_json_backend_is_fail_closed(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('RN_BACKEND', 'json')
    repo = ResumeRepository()
    with pytest.raises(RuntimeError, match='PRODUCTION_JSON_RESUME_DB_FORBIDDEN'):
        repo.search_candidates(limit=3)


def test_dev_json_fixture_is_not_workspace_resume_db(monkeypatch):
    src = (ROOT / 'runtime/rn/repositories/resume_repository.py').read_text(encoding='utf-8')
    assert 'workspace/resume_db.json' not in src
    assert 'data/dev/resume_db.fixture.json' in src
