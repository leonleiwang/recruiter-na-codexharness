"""邮件附件 → 简历库（mail intake）。

设计（对齐 Recruiter-Na 就医邮箱链路）：
- IMAP 只读：UID 游标 + MIME 附件抽取；附件以 SHA-256 去重；
- 受控路由：邮件带 X-Recruiter-Na-Listing-ID / X-Moka-Listing-ID 头才自动关联岗位；
  无头的邮件进 REVIEW_REQUIRED 池，绝不靠主题/文件名/发件人猜岗位；
- 本地测试：watch-local 监视一个收件目录，把 .pdf/.docx/.txt/.md 当附件入库。

云部署时把 IMapIntake 接上真实邮箱，把解析换成 Recruiter-Na 的 PDF/DOCX 解析 + LLM 结构化抽取（可选）。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from rn.resume_screening.parsing.resume_db import JsonResumeStore, parse_text_resume, sha256_of_text


@dataclass
class MailIntake:
    store: JsonResumeStore
    inbox_dir: Optional[Path] = None
    processed_dir: Optional[Path] = None
    _seen: set = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.inbox_dir:
            self.inbox_dir = Path(self.inbox_dir)
            self.inbox_dir.mkdir(parents=True, exist_ok=True)
        if self.processed_dir:
            self.processed_dir = Path(self.processed_dir)
            self.processed_dir.mkdir(parents=True, exist_ok=True)

    def watch_local(self, once: bool = True, limit: int = 50) -> list:
        if not self.inbox_dir:
            raise ValueError("inbox_dir 未设置")
        ingested = []
        for f in sorted(self.inbox_dir.iterdir())[:limit]:
            if not f.is_file() or f.name.startswith("."):
                continue
            if f.suffix.lower() not in (".pdf", ".docx", ".txt", ".md", ".eml"):
                continue
            rec = self.ingest_file(f)
            if rec:
                ingested.append(rec)
        return ingested

    def ingest_file(self, path: Path, meta: Optional[dict] = None) -> Optional[dict]:
        path = Path(path)
        meta = meta or self._read_sidecar(path)
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        for r in self.store.all_resumes():
            if r.get("sha256") == digest:
                return None
        suffix = path.suffix.lower()
        if suffix in (".txt", ".md"):
            text = data.decode("utf-8", errors="replace")
        elif suffix in (".pdf", ".docx"):
            text = _extract_docx_or_pdf(path)
        elif suffix == ".eml":
            text, meta = _parse_eml(data, path.name, meta)
        else:
            return None
        resume_id = "R-" + digest[:10]
        resume = parse_text_resume(text, resume_id=resume_id, source="email" if meta.get("email") else "folder",
                                   created_at=time.strftime("%Y-%m-%dT%H:%M:%S"), meta=meta)
        if not resume.get("raw_text") or len(resume["raw_text"]) < 20:
            return None
        listing = (meta.get("routing") or {}).get("listing_id")
        resume["routing"] = meta.get("routing") or {}
        if listing:
            resume["status"] = "NEW"
            resume["routing"]["job_id"] = listing
        else:
            resume["status"] = "REVIEW_REQUIRED"
            resume["routing"]["note"] = "无受控招聘编号，需人工归岗"
        from rn.resume_screening.decision.job_contract_v3 import normalize_resume_facts
        from rn.resume_screening.scoring.scoring_engine_v3 import calc_rq
        detail = calc_rq(normalize_resume_facts(resume), as_of=resume.get("created_at"))
        resume["rq"] = {"resume_quality_score": detail["score"], "breakdown": detail}
        self.store.upsert(resume)
        return resume

    def _read_sidecar(self, path: Path) -> dict:
        side = path.with_suffix(path.suffix + ".meta.json")
        if side.exists():
            try:
                return json.loads(side.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}


class IMapIntake:
    """真实 IMAP 接口骨架（云部署时实现）。
    connect() -> UID 游标恢复；fetch_new() -> [(uid, from_, subject, attachments, headers)]；
    附件 -> MailIntake.ingest_file 的内存版。只读 + 每封提交成功后推进 UID 游标。
    """

    def __init__(self, host: str, port: int = 993, user: str = "", password_env: str = "MAILBOX_PASSWORD"):
        self.host, self.port, self.user = host, port, user
        self.password_env = password_env

    def fetch_headers(self, raw: bytes) -> dict:
        text = raw.decode("utf-8", errors="replace")
        headers = {}
        for line in text.split("\r\n")[:400]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        return headers

    def extract_listing(self, headers: dict) -> str:
        for key in ("x-recruiter-na-listing-id", "x-moka-listing-id"):
            if headers.get(key):
                return headers[key].strip()
        return ""


def _extract_docx_or_pdf(path: Path) -> str:
    try:
        if path.suffix.lower() == ".docx":
            import docx
            doc = docx.Document(str(path))
            return "\n".join(p.text for p in doc.paragraphs if p.text)
        if path.suffix.lower() == ".pdf":
            try:
                import pypdf
            except ImportError:
                import PyPDF2 as pypdf
            reader = pypdf.PdfReader(str(path))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        return "[DOC_PARSE_PENDING] " + path.name + ": " + str(exc)
    return "[DOC_PARSE_PENDING] " + path.name


def _parse_eml(data: bytes, fname: str, meta: dict) -> tuple:
    import email
    from email import policy
    msg = email.message_from_bytes(data, policy=policy.default)
    headers = {k.lower(): v for k, v in msg.items()}
    meta = dict(meta or {})
    meta["email"] = {"from": headers.get("From", ""), "subject": headers.get("Subject", ""),
                     "date": headers.get("Date", "")}
    listing = ""
    for key in ("x-recruiter-na-listing-id", "x-moka-listing-id"):
        if headers.get(key):
            listing = headers[key].strip()
            break
    body_parts = []
    attachments = []
    for part in msg.walk():
        if part.get_content_disposition() == "attachment":
            attachments.append((part.get_filename() or "", part.get_payload(decode=True) or b""))
        elif part.get_content_type() == "text/plain":
            body_parts.append(part.get_content() or "")
    text = "\n".join(body_parts)
    if not text and attachments:
        text = attachments[0][1].decode("utf-8", errors="replace")
    if listing:
        meta["routing"] = {"listing_id": listing}
    return text, meta