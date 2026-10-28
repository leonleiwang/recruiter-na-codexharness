"""Prepare an authorized selected candidate's original resume for delivery."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Iterable


class ResumeDeliveryError(RuntimeError):
    """A safe, stable reason an original resume cannot be delivered."""


class SelectedResumeDeliveryService:
    def __init__(self, resume_repo: Any, *, allowed_roots: Iterable[str | Path] | None = None,
                 max_bytes: int | None = None):
        configured = list(allowed_roots or [x for x in os.environ.get("RECRUITER_RESUME_SOURCE_ROOTS", "").split(":") if x])
        self.resume_repo = resume_repo
        self.allowed_roots = [Path(root).resolve() for root in configured]
        self.max_bytes = int(max_bytes or os.environ.get("RECRUITER_RESUME_MAX_BYTES", str(20 * 1024 * 1024)))

    def prepare(self, request: dict[str, Any]) -> dict[str, str]:
        # 未配置根目录时不得凭猜测投递任何文件；原因要能被运维直接看懂。
        if not self.allowed_roots:
            raise ResumeDeliveryError("RESUME_SOURCE_ROOTS_NOT_CONFIGURED")
        candidate_id = str(request.get("selected_candidate_id") or "")
        shown = {str(x) for x in request.get("recommended_candidate_ids") or []}
        target = next((x for x in request.get("ranking") or [] if str(x.get("candidate_id") or "") == candidate_id), None)
        source_ref = str((target or {}).get("source_resume_ref") or "")
        if not candidate_id or candidate_id not in shown or not source_ref:
            raise ResumeDeliveryError("RESUME_DELIVERY_NOT_AUTHORIZED")
        row = self.resume_repo.get_candidate(source_ref)
        if not row:
            raise ResumeDeliveryError("RESUME_SOURCE_NOT_FOUND")
        raw_path = str(row.get("_mysql_source_file_path") or "")
        if not raw_path:
            raise ResumeDeliveryError("RESUME_SOURCE_PATH_MISSING")
        source = Path(raw_path).resolve()
        if not any(source.is_relative_to(root) for root in self.allowed_roots):
            raise ResumeDeliveryError("RESUME_SOURCE_OUTSIDE_ALLOWED_ROOT")
        if not source.is_file():
            raise ResumeDeliveryError("RESUME_SOURCE_NOT_REGULAR_FILE")
        if source.stat().st_size > self.max_bytes:
            raise ResumeDeliveryError("RESUME_SOURCE_TOO_LARGE")
        filename = Path(str(row.get("_mysql_file_name") or source.name)).name
        if not filename:
            raise ResumeDeliveryError("RESUME_SOURCE_FILENAME_MISSING")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        return {"candidate_id": candidate_id, "filename": filename, "file_path": str(source), "fingerprint_sha256": digest}
