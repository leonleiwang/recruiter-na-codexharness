"""端到端编排：简历文件 → 文本 → 字段 → 三维标签 → 三维要求 → 匹配报告。

调用链（全部为确定性代码，不调用 LLM）::

    ingest.document.load_document(path)
        → analysis.fields.extract_resume_fields(text)
        → analysis.tags.extract_resume_tags(fields)
        → analysis.requirements.build_job_requirements(job / job_text / policy)
        → analysis.matching.match_requirements(requirements, tags, fields)

设计约束：
- 匹配报告只是「辅助参考视图」，不写回 v5 决策字段、不产生录用建议；
- 所有中间结果都带证据（原文片段 / 命中的线索词 / 要求编号），便于人工复核；
- 报告是 JSON 可序列化的，可直接落盘归档（默认 ``workspace/runs/resume-screening-analysis``）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from rn.resume_screening.analysis import cues as cues_mod
from rn.resume_screening.analysis import fields as fields_mod
from rn.resume_screening.analysis import matching as matching_mod
from rn.resume_screening.analysis import requirements as requirements_mod
from rn.resume_screening.analysis import tags as tags_mod
from rn.resume_screening.paths import ANALYSIS_RUN_ROOT

SCHEMA_VERSION = "resume-screening-analysis/1.0"

#: 报告里允许出现的文件名字符（防 ``../../`` 之类的路径逃逸写盘）。
_SAFE_COMPONENT_RE = re.compile(r"[^0-9A-Za-z_\u4e00-\u9fff.\-]+")


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _safe_component(value: str, default: str = "analysis") -> str:
    """把用户可控字符串收敛成安全的单层文件名（不允许路径分隔符）。"""
    name = _SAFE_COMPONENT_RE.sub("_", Path(str(value or "")).name).strip("._-")
    return name[:80] or default


def _display_path(path: str | Path) -> str:
    """报告只展示文件名，不泄露本机绝对路径。"""
    text = str(path or "")
    try:
        return Path(text).name or text
    except (OSError, ValueError):
        return text


def sanitize_error(exc: BaseException, path: str | Path = "") -> str:
    """异常文本去掉本机绝对路径（报告可能被归档/分享）。"""
    detail = f"{type(exc).__name__}: {exc}"
    cwd = ""
    try:
        cwd = str(Path.cwd())
    except OSError:
        pass
    for absolute in (str(path or ""), cwd):
        if absolute:
            detail = detail.replace(absolute + os.sep, "").replace(absolute, "")
    return detail.strip(" :") or type(exc).__name__


def _atomic_write_text(path: Path, text: str) -> None:
    """先写临时文件再原子替换，避免中途失败留下半截报告。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _document_payload(document: Any) -> dict[str, Any]:
    """Convert a DocumentText into a JSON-safe, text-free summary (text kept separately)."""
    if document is None:
        return {}
    return {
        # 报告可能被归档/分享：只保留文件名，不写本机绝对路径。
        "source_path": _display_path(str(getattr(document, "source_path", "") or "")),
        "filename": str(getattr(document, "filename", "") or ""),
        "media_type": str(getattr(document, "media_type", "") or ""),
        "page_count": int(getattr(document, "page_count", 0) or 0),
        "engines": list(getattr(document, "engines", []) or []),
        "warnings": list(getattr(document, "warnings", []) or []),
        "text_chars": len(str(getattr(document, "text", "") or "")),
        "meta": dict(getattr(document, "meta", {}) or {}),
    }


def _unique_notes(values: Iterable[Any]) -> list[str]:
    """去重但保持顺序的 notes 合并（requirements/warnings 与 tags/matching 的说明会重叠）。"""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def analyze_document(
    document: Any,
    *,
    job: str = "",
    job_text: str = "",
    policy: dict[str, Any] | str | Path | None = None,
    profile_id: str = "",
    requirements_doc: dict[str, Any] | None = None,
    strict_requirements: bool = False,
) -> dict[str, Any]:
    """Analyze one already-loaded document end to end."""
    text = str(getattr(document, "text", "") or "")
    fields = fields_mod.extract_resume_fields(text, source_name=str(getattr(document, "filename", "") or ""))
    tags_doc = tags_mod.extract_resume_tags(fields)
    if isinstance(requirements_doc, dict):
        req_doc = requirements_doc
        if strict_requirements:
            requirements_mod.ensure_requirements(req_doc, True)
    else:
        req_doc = requirements_mod.build_job_requirements(
            job=job,
            job_text=job_text,
            policy=policy,
            profile_id=profile_id,
            strict=strict_requirements,
        )
    match_doc = matching_mod.match_requirements(req_doc, tags_doc, fields)
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now(),
        "job": req_doc.get("job_name") or job,
        "profile_id": profile_id,
        "document": _document_payload(document),
        "fields": fields,
        "tags": tags_doc,
        "requirements": req_doc,
        "matching": match_doc,
        "summary": tags_doc.get("summary") or {},
        # 要求侧的警告（未知岗位 / JD 未解析 / 敏感条件剔除）必须进入报告说明，
        # 否则「0 条要求」会静默变成「0.0% 低匹配」。
        "notes": _unique_notes(
            list(tags_doc.get("notes") or [])
            + list(req_doc.get("warnings") or [])
            + list(match_doc.get("notes") or [])
        ),
        "disclaimer": matching_mod.DISCLAIMER,
    }
    return report


def analyze_text(
    text: str,
    *,
    filename: str = "",
    media_type: str = "text",
    job: str = "",
    job_text: str = "",
    policy: dict[str, Any] | str | Path | None = None,
    profile_id: str = "",
    requirements_doc: dict[str, Any] | None = None,
    strict_requirements: bool = False,
) -> dict[str, Any]:
    """Analyze raw resume text (no file IO, no OCR)."""
    document = _SimpleDocument(
        source_path="",
        filename=filename or "inline.txt",
        media_type=media_type,
        text=str(text or ""),
        page_count=1,
        engines=["text"],
        warnings=[],
        meta={"inline": True},
    )
    return analyze_document(
        document,
        job=job,
        job_text=job_text,
        policy=policy,
        profile_id=profile_id,
        requirements_doc=requirements_doc,
        strict_requirements=strict_requirements,
    )


class _SimpleDocument:
    """Tiny stand-in for DocumentText so analyze_text does not need the ingest layer."""

    __slots__ = ("source_path", "filename", "media_type", "text", "page_count", "engines", "warnings", "meta")

    def __init__(self, **kwargs: Any) -> None:
        for key in self.__slots__:
            setattr(self, key, kwargs.get(key))


def analyze_resume_file(
    path: str | Path,
    *,
    job: str = "",
    job_text: str = "",
    policy: dict[str, Any] | str | Path | None = None,
    profile_id: str = "",
    requirements_doc: dict[str, Any] | None = None,
    ocr_engine: Any = None,
    strict_requirements: bool = False,
) -> dict[str, Any]:
    """Analyze one resume file (PDF / DOCX / image / text)."""
    # ingest 层依赖 fitz / docx / PIL 等较重依赖，延迟导入让 ``--help`` 与纯文本场景保持轻量。
    from rn.resume_screening.ingest.document import load_document

    document = load_document(path, ocr_engine=ocr_engine)
    return analyze_document(
        document,
        job=job,
        job_text=job_text,
        policy=policy,
        profile_id=profile_id,
        requirements_doc=requirements_doc,
        strict_requirements=strict_requirements,
    )


def analyze_resume_bytes(
    data: bytes,
    *,
    filename: str,
    media_type: str | None = None,
    job: str = "",
    job_text: str = "",
    policy: dict[str, Any] | str | Path | None = None,
    profile_id: str = "",
    requirements_doc: dict[str, Any] | None = None,
    ocr_engine: Any = None,
    strict_requirements: bool = False,
) -> dict[str, Any]:
    """Analyze an uploaded resume without touching disk (图片/Word/PDF 上传流用这个入口)."""
    from rn.resume_screening.ingest.document import load_bytes

    document = load_bytes(data, filename, media_type=media_type, ocr_engine=ocr_engine)
    return analyze_document(
        document,
        job=job,
        job_text=job_text,
        policy=policy,
        profile_id=profile_id,
        requirements_doc=requirements_doc,
        strict_requirements=strict_requirements,
    )


def _cache_digest(path: str | Path, requirements_doc: dict[str, Any]) -> str:
    """Content-addressed cache key: file bytes + the requirements it was scored against."""
    digest = hashlib.sha256()
    try:
        digest.update(Path(path).read_bytes())
    except OSError:
        digest.update(str(path).encode("utf-8", "replace"))
    fingerprint = json.dumps(
        {
            "jd_id": requirements_doc.get("jd_id") or "",
            "source": requirements_doc.get("source") or "",
            "requirements": requirements_doc.get("requirements") or [],
            "hard_filters": requirements_doc.get("hard_filters") or {},
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    digest.update(fingerprint.encode("utf-8"))
    return digest.hexdigest()[:32]


def analyze_resume_files(
    paths: Iterable[str | Path],
    *,
    job: str = "",
    job_text: str = "",
    policy: dict[str, Any] | str | Path | None = None,
    profile_id: str = "",
    requirements_doc: dict[str, Any] | None = None,
    ocr_engine: Any = None,
    strict_requirements: bool = False,
    cache_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Analyze several resumes against the same job and rank them by tag hit rate.

    ``cache_dir`` 打开按「文件内容 + 岗位要求」寻址的结果缓存：同一份简历在同一份
    JD 下重跑会直接复用上次的解析/匹配结果，100+ 份批处理和断点续跑不用重新 OCR。
    """
    # 岗位要求只解析一次；strict 时空要求直接抛错，避免整批都变成「0 条要求」。
    if isinstance(requirements_doc, dict):
        req_doc = requirements_doc
        if strict_requirements:
            requirements_mod.ensure_requirements(req_doc, True)
    else:
        req_doc = requirements_mod.build_job_requirements(
            job=job,
            job_text=job_text,
            policy=policy,
            profile_id=profile_id,
            strict=strict_requirements,
        )
    reports: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    cache_root = Path(cache_dir).expanduser() if cache_dir else None
    cache_hits = 0
    cache_misses = 0
    if cache_root is not None:
        cache_root.mkdir(parents=True, exist_ok=True)
    for path in paths:
        cache_file: Path | None = None
        if cache_root is not None:
            cache_file = cache_root / f"{_cache_digest(path, req_doc)}.json"
            if cache_file.exists():
                try:
                    cached = json.loads(cache_file.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    cached = None
                if isinstance(cached, dict) and cached.get("matching"):
                    reports.append(cached)
                    cache_hits += 1
                    continue
        try:
            report = analyze_document(
                _load_document_for(path, ocr_engine=ocr_engine),
                job=job,
                job_text=job_text,
                policy=policy,
                profile_id=profile_id,
                requirements_doc=req_doc,
            )
            reports.append(report)
            if cache_file is not None:
                _atomic_write_text(cache_file, json.dumps(report, ensure_ascii=False, indent=2))
                cache_misses += 1
        except Exception as exc:  # noqa: BLE001 - 单个文件失败不应中断整批核对
            # 失败明细会进报告：只留文件名，并清掉本机绝对路径。
            errors.append(
                {
                    "path": _display_path(path),
                    "error": sanitize_error(exc, path),
                }
            )
    ranked = sorted(
        reports,
        key=lambda report: matching_mod.match_rank_key(report.get("matching") or {}),
        reverse=True,
    )
    ranking = []
    for index, report in enumerate(ranked, start=1):
        matching = report.get("matching") or {}
        ranking.append(
            {
                "rank": index,
                "filename": (report.get("document") or {}).get("filename", ""),
                "name": (report.get("summary") or {}).get("name", ""),
                "overall_percent": matching.get("overall_percent"),
                "label": matching.get("label", ""),
                # 对外只在表格里显示推荐结论（推荐/待核实/不推荐）；档位 label 保留为内部字段。
                "recommendation": matching.get("recommendation", ""),
                "tag_hit_count": matching.get("tag_hit_count"),
                "tag_applied_count": matching.get("tag_applied_count"),
                "tag_hit_percent": matching.get("tag_hit_percent"),
                # 排序结果先给「命中多少」，再列出待验证项（面试/证件核验），
                # 「未命中（有反证）」单独一列，避免把「没写」当成不会。
                "tag_weak_count": matching.get("tag_weak_count", 0),
                "tag_missing_count": matching.get("tag_missing_count", 0),
                "tag_contradicted_count": matching.get("tag_contradicted_count", 0),
                "pending_verification": [
                    item.get("name")
                    for item in (matching.get("verification_items") or [])
                    if item.get("name")
                ],
                "disqualified": bool(matching.get("disqualified")),
                "matched_must_have": matching.get("matched_must_have", []),
                "dimension_coverage": {
                    dim: block.get("coverage_percent")
                    for dim, block in (matching.get("dimensions") or {}).items()
                },
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now(),
        "job": req_doc.get("job_name") or job,
        "profile_id": profile_id,
        "jd_id": req_doc.get("jd_id") or "",
        "requirements_source": req_doc.get("source") or "",
        "requirements": req_doc,
        "ranking": ranking,
        "reports": ranked,
        "errors": errors,
        "cache": {"enabled": cache_root is not None, "hits": cache_hits, "misses": cache_misses},
        "disclaimer": matching_mod.DISCLAIMER,
    }


def _load_document_for(path: str | Path, *, ocr_engine: Any = None) -> Any:
    from rn.resume_screening.ingest.document import load_document

    return load_document(path, ocr_engine=ocr_engine)


def render_report_markdown(report: dict[str, Any]) -> str:
    """Render one analysis report as Markdown (fields + tags + match)."""
    lines: list[str] = []
    document = report.get("document") or {}
    summary = report.get("summary") or {}
    title = summary.get("name") or document.get("filename") or "简历"
    lines.append(f"# 简历解析与匹配：{title}")
    lines.append("")
    lines.append(f"- 岗位：{report.get('job') or '（未指定）'}")
    matching_block = report.get("matching") or {}
    if matching_block.get("jd_id"):
        lines.append(f"- 岗位要求：生成 JD {matching_block['jd_id']}（按标签命中率计分）")
    lines.append(f"- 文件：{document.get('filename','')}（{document.get('media_type','')}，{document.get('page_count',0)} 页）")
    lines.append(f"- 解析引擎：{'、'.join(document.get('engines') or []) or '—'}　文本长度：{document.get('text_chars',0)} 字")
    if document.get("warnings"):
        lines.append(f"- 解析警告：{'；'.join(document['warnings'])}")
    lines.append("")
    lines.append("## 解析字段")
    lines.append("")
    fields = report.get("fields") or {}
    education = fields.get("education") if isinstance(fields.get("education"), dict) else {}
    rows: list[tuple[str, Any]] = [
        ("姓名", fields.get("name")),
        ("电话", fields.get("phone")),
        ("邮箱", fields.get("email")),
        ("性别", fields.get("gender")),
        ("年龄", fields.get("age")),
        ("现居地", fields.get("location")),
        ("学历", education.get("level")),
        ("院校", education.get("school")),
        ("专业", education.get("major")),
        ("毕业年份", education.get("graduation_year")),
        ("工作年限", fields.get("total_work_years")),
        ("当前公司", fields.get("current_company")),
        ("当前职位", fields.get("current_position")),
        ("求职意向", fields.get("expected_position")),
        ("期望城市", fields.get("expected_city")),
        ("期望薪资", fields.get("expected_salary")),
        ("证书", "、".join(fields.get("certifications") or [])),
        ("技能", "、".join(fields.get("skills") or [])),
    ]
    lines.append("| 字段 | 内容 |")
    lines.append("| --- | --- |")
    for label, value in rows:
        if value in (None, "", []):
            continue
        lines.append(f"| {label} | {value} |")
    lines.append("")
    experiences = fields.get("work_experiences") or []
    if experiences:
        lines.append("### 工作经历")
        lines.append("")
        lines.append("| 时间 | 公司 | 职位 |")
        lines.append("| --- | --- | --- |")
        for item in experiences:
            period = "-".join(part for part in (str(item.get("start_date") or ""), str(item.get("end_date") or "")) if part) or "—"
            if item.get("duration_estimated"):
                period += "（月份不完整，时长估算）"
            lines.append(f"| {period} | {item.get('company','')} | {item.get('position','')} |")
        lines.append("")
    if fields.get("missing_fields"):
        lines.append(f"> 未提取到的字段：{'、'.join(fields['missing_fields'])}")
        lines.append("")
    tags_doc = report.get("tags") or {}
    lines.append("## 简历三维标签（特质不计分，仅参考）")
    lines.append("")
    lines.append("| 维度 | 标签 |")
    lines.append("| --- | --- |")
    for dim, label in cues_mod.DIMENSION_LABELS.items():
        names = [row.get("name", "") for row in (tags_doc.get("tags") or {}).get(dim) or []]
        if names:
            lines.append(f"| {label} | {'、'.join(names)} |")
    lines.append("")
    lines.append(matching_mod.render_matching_markdown(
        report.get("matching") or {}, resume_summary=summary, include_notes=False,
    ))
    if report.get("notes"):
        lines.append("## 说明")
        lines.append("")
        for note in report["notes"]:
            lines.append(f"- {note}")
        lines.append("")
    return "\n".join(lines)


def write_report(
    report: dict[str, Any],
    *,
    out_dir: str | Path | None = None,
    stem: str = "analysis",
    overwrite: bool = False,
) -> dict[str, str]:
    """Persist ``analysis.json`` + ``report.md``; returns the written paths.

    ``stem`` 会收敛成安全文件名（不允许路径分隔符），并且默认**不覆盖**已有报告：
    重复分析请换 ``out_dir`` 或显式传 ``overwrite=True``，避免静默覆盖历史结论。
    """
    base = Path(out_dir) if out_dir else ANALYSIS_RUN_ROOT / datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_stem = _safe_component(stem, "analysis")
    json_path = base / f"{safe_stem}.json"
    md_path = base / f"{safe_stem}.md"
    if not overwrite:
        existing = [path for path in (json_path, md_path) if path.exists()]
        if existing:
            raise FileExistsError(f"报告已存在：{_display_path(existing[0])}；如需覆盖请传 overwrite=True")
    _atomic_write_text(json_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    _atomic_write_text(md_path, render_report_markdown(report) + "\n")
    return {"json": str(json_path), "markdown": str(md_path), "dir": str(base)}


def write_batch_report(
    batch: dict[str, Any],
    *,
    out_dir: str | Path | None = None,
    overwrite: bool = False,
) -> dict[str, str]:
    """Persist a batch analysis (ranking + per-resume sections); refuses to clobber by default."""
    base = Path(out_dir) if out_dir else ANALYSIS_RUN_ROOT / datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = base / "batch.json"
    md_path = base / "batch.md"
    if not overwrite:
        existing = [path for path in (json_path, md_path) if path.exists()]
        if existing:
            raise FileExistsError(f"报告已存在：{_display_path(existing[0])}；如需覆盖请传 overwrite=True")
    _atomic_write_text(json_path, json.dumps(batch, ensure_ascii=False, indent=2) + "\n")
    parts: list[str] = ["# 批量简历匹配（辅助参考）", ""]
    parts.append(f"- 岗位：{batch.get('job') or '（未指定）'}")
    parts.append(f"- 简历数：{len(batch.get('reports') or [])}，失败：{len(batch.get('errors') or [])}")
    parts.append("")
    parts.append("## 排序总览")
    parts.append("")
    parts.append("先看命中了几项，再看还剩几项待验证（面试/证件核验时确认，没写不等于不会）。")
    parts.append("")
    parts.append("| 排名 | 姓名 | 文件 | 命中标签 | 待验证项 | 未命中（反证） | 结论（推荐/待核实/不推荐）|")
    parts.append("| --- | --- | --- | --- | --- | --- | --- |")
    for row in batch.get("ranking") or []:
        hit = row.get("tag_hit_count") or 0
        applied = row.get("tag_applied_count") or 0
        pending = row.get("pending_verification") or []
        pending_text = f"{len(pending)} 项" + ("：" + "、".join(pending) if pending else "")
        parts.append(
            f"| {row.get('rank')} | {row.get('name','')} | {row.get('filename','')} "
            f"| {hit}/{applied}（{matching_mod.percent_text(row.get('overall_percent'))}） "
            f"| {pending_text} | {row.get('tag_contradicted_count') or 0} 项 "
            f"| {row.get('recommendation') or '—'} |"
        )
    parts.append("")
    if batch.get("errors"):
        parts.append("## 失败文件")
        parts.append("")
        for error in batch["errors"]:
            parts.append(f"- {error.get('path')}：{error.get('error')}")
        parts.append("")
    for report in batch.get("reports") or []:
        parts.append("---")
        parts.append("")
        parts.append(render_report_markdown(report))
    parts.append("---")
    parts.append(batch.get("disclaimer") or matching_mod.DISCLAIMER)
    parts.append("")
    _atomic_write_text(md_path, "\n".join(parts) + "\n")
    return {"json": str(json_path), "markdown": str(md_path), "dir": str(base)}


__all__ = [
    "SCHEMA_VERSION",
    "analyze_document",
    "analyze_resume_bytes",
    "analyze_resume_file",
    "analyze_resume_files",
    "analyze_text",
    "render_report_markdown",
    "write_batch_report",
    "write_report",
]
