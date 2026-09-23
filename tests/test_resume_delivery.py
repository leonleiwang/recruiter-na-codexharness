from pathlib import Path

import pytest

from rn.resume_delivery import ResumeDeliveryError, SelectedResumeDeliveryService
from rn.repositories import ResumeRepository


class FakeResumeRepo:
    def __init__(self, row):
        self.row = row

    def get_candidate(self, identifier):
        return self.row if identifier == "42" else None


def request():
    return {
        "request_id": "REQ-1",
        "selected_candidate_id": "C1",
        "recommended_candidate_ids": ["C1"],
        "ranking": [{"candidate_id": "C1", "source_resume_ref": "42", "name": "候选人"}],
    }


def test_prepare_selected_resume_only_returns_authorized_regular_file_under_configured_root(tmp_path: Path):
    source = tmp_path / "resume.pdf"
    source.write_bytes(b"%PDF-test")
    service = SelectedResumeDeliveryService(FakeResumeRepo({"_mysql_source_file_path": str(source), "_mysql_file_name": "candidate.pdf"}), allowed_roots=[tmp_path])

    prepared = service.prepare(request())

    assert prepared["candidate_id"] == "C1"
    assert prepared["filename"] == "candidate.pdf"
    assert prepared["file_path"] == str(source.resolve())
    assert len(prepared["fingerprint_sha256"]) == 64


def test_prepare_rejects_file_outside_configured_root(tmp_path: Path):
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"test")
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    service = SelectedResumeDeliveryService(FakeResumeRepo({"_mysql_source_file_path": str(outside), "_mysql_file_name": "candidate.pdf"}), allowed_roots=[allowed])

    with pytest.raises(ResumeDeliveryError, match="RESUME_SOURCE_OUTSIDE_ALLOWED_ROOT"):
        service.prepare(request())


def test_unconfigured_source_roots_disable_delivery_with_an_actionable_reason(tmp_path: Path, monkeypatch):
    """ISSUE-003：未配置 RECRUITER_RESUME_SOURCE_ROOTS 时必须显式失败。"""
    source = tmp_path / "resume.pdf"
    source.write_bytes(b"%PDF-test")
    monkeypatch.delenv("RECRUITER_RESUME_SOURCE_ROOTS", raising=False)
    service = SelectedResumeDeliveryService(
        FakeResumeRepo({"_mysql_source_file_path": str(source), "_mysql_file_name": "candidate.pdf"})
    )

    assert service.allowed_roots == []
    with pytest.raises(ResumeDeliveryError, match="RESUME_SOURCE_ROOTS_NOT_CONFIGURED"):
        service.prepare(request())


def test_numeric_delivery_reference_uses_mysql_primary_key_lookup():
    class Backend:
        def __init__(self):
            self.get_calls = []
            self.search_calls = []

        def get(self, identifier):
            self.get_calls.append(identifier)
            return {"name": "顾琪", "_mysql_source_file_path": "/safe/guqi.pdf"}

        def search(self, query, **_kwargs):
            self.search_calls.append(query)
            return [{"name": "黄永顺", "_mysql_source_file_path": "/safe/huang.pdf"}]

    backend = Backend()
    repository = ResumeRepository(backend="mysql")
    repository._mysql = backend

    candidate = repository.get_candidate("42")

    assert candidate["name"] == "顾琪"
    assert backend.get_calls == ["42"]
    assert backend.search_calls == []
