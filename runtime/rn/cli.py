"""Recruiter-Na v5.0 CLI for local diagnostics and resume-library maintenance.

The active v5 CLI has no candidate scoring command. Candidate decisions are
executed only through the host hiring workflow so the accepted role policy,
semantic evidence grounding, and deterministic decision rules stay together.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from rn.criteria_adapter_v5 import load_inspection_technician_policy
from rn.resume_db import JsonResumeStore, ResumeHttpBackend, parse_text_resume, sha256_of_bytes

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = str(ROOT / "data" / "dev" / "resume_db.fixture.json")


def _store(args: argparse.Namespace) -> Any:
    db = getattr(args, "db", None) or DEFAULT_DB
    if args.backend == "http":
        return ResumeHttpBackend(args.http_url)
    if args.backend == "mysql":
        from rn.resume_mysql import ResumeMySQLBackend
        return ResumeMySQLBackend(
            host=args.mysql_host, port=args.mysql_port, user=args.mysql_user,
            password=args.mysql_password, database=args.mysql_database,
        )
    if os.environ.get("APP_ENV", "development").lower() == "production":
        raise RuntimeError("PRODUCTION_JSON_RESUME_DB_FORBIDDEN")
    return JsonResumeStore(Path(db))


def _load_dev_resumes(dev_dir: str) -> list[dict[str, Any]]:
    from rn.mail_watch import _extract_docx_or_pdf
    folder = Path(dev_dir)
    if not folder.is_dir():
        raise SystemExit(f"dev-dir 不是目录: {folder}")
    out: list[dict[str, Any]] = []
    for f in sorted(folder.iterdir()):
        if not f.is_file() or f.suffix.lower() not in (".pdf", ".docx", ".txt", ".md"):
            continue
        data = f.read_bytes()
        text = data.decode("utf-8", errors="replace") if f.suffix.lower() in (".txt", ".md") else _extract_docx_or_pdf(f)
        if not text or "[DOC_PARSE_PENDING" in text or len(text.strip()) < 20:
            continue
        resume = parse_text_resume(
            text, resume_id="DEV-" + sha256_of_bytes(data)[:10], source="dev",
            created_at=time.strftime("%Y-%m-%dT%H:%M:%S"), meta={"filename": f.name},
        )
        resume["file"] = f.name
        out.append(resume)
    return out


def _dev_search(rows: list[dict[str, Any]], q: str = "", degree: str | None = None,
                years_min: int | None = None, location: str | None = None,
                tags: list[str] | None = None, certs: list[str] | None = None,
                limit: int = 20) -> list[dict[str, Any]]:
    tags, certs = tags or [], certs or []
    out: list[dict[str, Any]] = []
    for r in rows:
        if degree and degree not in str((r.get("education") or {}).get("level") or ""):
            continue
        if years_min is not None and float(r.get("total_work_years") or 0) < years_min:
            continue
        if location and location not in str(r.get("location") or ""):
            continue
        hay = json.dumps(r, ensure_ascii=False).lower()
        if tags and not all(str(t).lower() in hay for t in tags):
            continue
        have_certs = {str(c).lower() for c in (r.get("certifications") or [])}
        if certs and not all(str(c).lower() in have_certs for c in certs):
            continue
        if q and not all(word.lower() in hay for word in q.split()):
            continue
        out.append(r)
        if len(out) >= limit:
            break
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rn", description="Recruiter-Na v5 Candidate Decision support CLI")
    p.add_argument("--db", default=DEFAULT_DB, help="开发测试 JSON fixture；生产禁用")
    p.add_argument("--backend", choices=["json", "http", "mysql"], default=os.environ.get("RN_BACKEND", "mysql"))
    p.add_argument("--http-url", default=os.environ.get("RESUME_DB_API_URL", "http://127.0.0.1:8787"))
    p.add_argument("--mysql-host", default=os.environ.get("MYSQL_HOST", "127.0.0.1"))
    p.add_argument("--mysql-port", type=int, default=int(os.environ.get("MYSQL_PORT", "3306")))
    p.add_argument("--mysql-user", default=os.environ.get("MYSQL_USER", "root"))
    p.add_argument("--mysql-password", default=os.environ.get("MYSQL_PASSWORD", ""))
    p.add_argument("--mysql-database", default=os.environ.get("MYSQL_DATABASE", ""))
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="简历库状态")
    sub.add_parser("policy", help="查看当前启用的检验技术员 Decision Policy 元信息")

    sp = sub.add_parser("ingest", help="入库文件或目录（不生成候选人分数）")
    sp.add_argument("target")
    sp.add_argument("--source", default="upload")
    sp.add_argument("--original-root", default=None, help="MySQL 原件所在受控根目录；生产入库必须指定")

    sp = sub.add_parser("search", help="只读检索候选；仅用于检索，不决定通过/不通过")
    sp.add_argument("--dev-dir", default=None)
    sp.add_argument("--q", default="")
    sp.add_argument("--degree", default=None)
    sp.add_argument("--years-min", type=int, default=None)
    sp.add_argument("--location", default=None)
    sp.add_argument("--tags", default="")
    sp.add_argument("--certs", default="")
    sp.add_argument("--limit", type=int, default=20)

    sp = sub.add_parser("dev-extract", help="抽取开发简历文本")
    sp.add_argument("dir")
    sp.add_argument("--out", default=None)
    return p


def _criteria_count_by_profile(policy: dict) -> dict[str, int]:
    """每个标准画像实际参与评估的判据数（policy 是两画像的并集）。"""
    all_names = [str(x.get("name") or "") for key in ("capabilities", "experiences", "traits")
                 for x in (policy.get(key) or [])]
    out: dict[str, int] = {}
    for profile_id, overrides in (policy.get("profile_criterion_overrides") or {}).items():
        excluded = {name for name, patch in (overrides or {}).items()
                    if isinstance(patch, dict) and patch.get("applicable") is False}
        out[profile_id] = len([n for n in all_names if n not in excluded])
    return out


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "policy":
        policy = load_inspection_technician_policy()
        print(json.dumps({
            "schema_version": policy.get("schema_version"),
            "policy_id": policy.get("policy_id"),
            "role": policy.get("role_name"),
            "supported_profile_ids": policy.get("supported_profile_ids"),
            "criteria_count": sum(len(policy.get(k) or []) for k in ("capabilities", "experiences", "traits")),
            "criteria_by_profile": _criteria_count_by_profile(policy),
            "decision_model": "PASS / VERIFY / FAIL; no numeric candidate score",
        }, ensure_ascii=False, indent=2))
        return 0

    store = _store(args)
    if args.cmd == "status":
        print(json.dumps(store.stats(), ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "ingest":
        from rn.mail_watch import _extract_docx_or_pdf
        target = Path(args.target)
        files = (
            sorted(
                (x for x in target.rglob("*") if x.is_file() and "__MACOSX" not in x.parts and not x.name.startswith("._")),
                key=lambda x: str(x),
            )
            if target.is_dir() else [target]
        )
        count = 0
        skipped = 0
        for f in files:
            if not f.is_file() or f.suffix.lower() not in (".txt", ".md", ".pdf", ".docx"):
                continue
            if args.backend == "mysql":
                if not args.original_root:
                    raise RuntimeError("MYSQL_INGEST_REQUIRES_ORIGINAL_ROOT")
                try:
                    outcome = store.ingest_original(f, source_root=args.original_root, fetched_by=args.source)
                except ValueError as exc:
                    print(f"skip {exc}: {f.name}", file=sys.stderr)
                    skipped += 1
                    continue
                print(f"{outcome['status']} {outcome['id']} {outcome['filename']}")
                count += 1
                continue
            data = f.read_bytes()
            text = data.decode("utf-8", errors="replace") if f.suffix.lower() in (".txt", ".md") else _extract_docx_or_pdf(f)
            if not text or "[DOC_PARSE_PENDING" in text:
                print(f"skip parse failed: {f.name}", file=sys.stderr)
                continue
            resume = parse_text_resume(text, resume_id="R-" + sha256_of_bytes(data)[:10], source=args.source,
                                       created_at=time.strftime("%Y-%m-%dT%H:%M:%S"), meta={"filename": f.name})
            store.upsert(resume)
            print(f"ingested {resume['resume_id']} {resume.get('name', '')}")
            count += 1
        print(f"done: {count}; skipped: {skipped}")
        return 0

    if args.cmd == "search":
        tags = [x.strip() for x in args.tags.split(",") if x.strip()]
        certs = [x.strip() for x in args.certs.split(",") if x.strip()]
        if args.dev_dir:
            rows = _dev_search(_load_dev_resumes(args.dev_dir), q=args.q, degree=args.degree, years_min=args.years_min,
                               location=args.location, tags=tags, certs=certs, limit=args.limit)
        else:
            rows = store.search(args.q, degree=args.degree, years_min=args.years_min, location=args.location,
                                tags=tags, certs=certs, limit=args.limit)
        for r in rows:
            print(f"{r.get('resume_id')}  {r.get('name', '')}  {(r.get('education') or {}).get('level', '')}  {r.get('total_work_years', 0)}年  {r.get('location', '')}")
        print(f"found {len(rows)}")
        return 0

    if args.cmd == "dev-extract":
        from rn.mail_watch import _extract_docx_or_pdf
        folder = Path(args.dir)
        if not folder.is_dir():
            print(f"dev-extract dir not found: {folder}", file=sys.stderr)
            return 1
        out_dir = Path(args.out) if args.out else None
        if out_dir:
            out_dir.mkdir(parents=True, exist_ok=True)
        for f in sorted(folder.iterdir()):
            if not f.is_file() or f.suffix.lower() not in (".pdf", ".docx", ".txt", ".md"):
                continue
            data = f.read_bytes()
            text = data.decode("utf-8", errors="replace") if f.suffix.lower() in (".txt", ".md") else _extract_docx_or_pdf(f)
            if not text or "[DOC_PARSE_PENDING" in text:
                print(f"{f.name} PARSE_FAILED")
                continue
            resume = parse_text_resume(text, resume_id="DEV-" + sha256_of_bytes(data)[:10], meta={"filename": f.name})
            print(f"{f.name} PARSE_OK name={resume.get('name')} chars={len(text)}")
            if out_dir:
                (out_dir / f"{f.stem}.txt").write_text(text, encoding="utf-8")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
