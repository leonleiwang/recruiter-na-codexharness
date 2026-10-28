"""简历解析 + JD 标签命中率匹配的命令行入口。

用法::

    # 单份简历（PDF/DOCX/图片/txt），用政策画像的岗位要求
    python -m rn.resume_screening.cli analyze 简历.pdf --job 检验技术员

    # 多份简历批量比对（按辅助匹配度排序，输出到 workspace/runs/...）
    python -m rn.resume_screening.cli analyze 简历A.docx 简历B.png --job 检验技术员 --out workspace/runs/demo

    # 自备 JD 文本（任意岗位）
    python -m rn.resume_screening.cli analyze 简历.pdf --jd-file jd.txt

    # 主链路：需求字段 → 生成 JD → JD 拆标签 → 简历按标签命中率评分（年龄是硬门槛）
    python -m rn.resume_screening.cli analyze 简历.pdf --fields-file fields.json --profile-id PROFILE-412167F2937C

    # 用已生成的 JD JSON 直接评分（跳过再生成）
    python -m rn.resume_screening.cli analyze 简历.pdf --jd-json workspace/runs/resume-screening-jd/xxx/jd.json

    # 只看政策拆出来的三维度要求
    python -m rn.resume_screening.cli requirements --job 检验技术员 --json

    # 从招聘需求字段生成结构化 JD（可继承标准画像、用相关模板做措辞参考）
    python -m rn.resume_screening.cli jd --fields-file fields.json --profile-id PROFILE-412167F2937C

    # 直接分析文本（不经文件解析）
    python -m rn.resume_screening.cli analyze --text "姓名：张三 电话：138..." --job 检验技术员 --json

退出码：0 成功；2 输入错误（文件不存在/无法解码/岗位要求为空/参数冲突）；3 分析或写盘失败。
"""
from __future__ import annotations

import argparse
import codecs
import datetime
import json
import sys
from pathlib import Path
from typing import Any

from rn.resume_screening import paths, service
from rn.resume_screening.analysis import matching as matching_mod
from rn.resume_screening.analysis import requirements as requirements_mod
from rn.resume_screening.jd import generator as jd_generator

EXIT_OK = 0
EXIT_INPUT_ERROR = 2
EXIT_ANALYSIS_ERROR = 3
EXIT_QUALITY_ERROR = 4


def _configure_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):  # pragma: no cover - piped/closed streams
            pass


def _decode_text_bytes(data: bytes) -> str:
    """按 BOM → UTF-8 → GB18030 的顺序解码文本；都失败则视为二进制/未知编码。"""
    if data.startswith(codecs.BOM_UTF16_LE) or data.startswith(codecs.BOM_UTF16_BE):
        return data.decode("utf-16")
    if data.startswith(codecs.BOM_UTF8):
        return data.decode("utf-8-sig")
    if data.startswith(codecs.BOM_UTF32_LE) or data.startswith(codecs.BOM_UTF32_BE):
        return data.decode("utf-32")
    for encoding in ("utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("文本文件无法解码（支持 UTF-8 / UTF-16 / GB18030）")


def _read_text_file(path: str) -> str:
    """读取 JD 等文本文件：兼容 UTF-8 / UTF-16 / GBK(GB18030)，不再只认 utf-8-sig。"""
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise ValueError(f"无法读取文件：{Path(path).name}（{exc.strerror or exc}）") from exc
    if not data.strip():
        return ""
    return _decode_text_bytes(data)


def _dump(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m rn.resume_screening.cli",
        description="简历解析（PDF/DOCX/图片/文本）→ 指定字段 → 能力/经验计分、特质仅参考的匹配（辅助参考，非录用评分）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser("analyze", help="解析简历并按 JD 标签命中率比对")
    analyze.add_argument("files", nargs="*", help="简历文件路径（PDF/DOCX/图片/txt）")
    analyze.add_argument("--text", default="", help="直接传入简历文本（与 files 二选一）")
    analyze.add_argument("--job", "-j", default="", help="岗位名称，如 检验技术员（命中政策画像）")
    analyze.add_argument("--jd-file", default="", help="岗位 JD 文本文件（任意岗位，覆盖 --job 的政策匹配）")
    analyze.add_argument(
        "--fields-file",
        default="",
        help="招聘需求字段 JSON：先生成 JD，再拆成标签按命中率评分（推荐的主链路）",
    )
    analyze.add_argument("--jd-json", default="", help="已生成的 JD JSON 文件（与 --fields-file 二选一）")
    analyze.add_argument("--request-id", default="LOCAL", help="生成 JD 时的招聘工单 ID（配合 --fields-file）")
    analyze.add_argument("--profile-file", default="", help="画像原始 JSON 文件（配合 --fields-file，优先于 --profile-id）")
    analyze.add_argument("--related-jd-title", default="", help="相关 JD 模板检索词（配合 --fields-file，只作措辞参考）")
    analyze.add_argument("--library-file", default="", help="JD 模板库 JSON 路径（配合 --fields-file）")
    analyze.add_argument("--profile-id", default="", help="画像 ID（应届 PROFILE-91603AA55F6E / 社招 PROFILE-412167F2937C）")
    analyze.add_argument("--policy", default="", help="政策 JSON 路径（默认 inspection_technician_v1.json）")
    analyze.add_argument("--out", default="", help="输出目录；不传则写入 workspace/runs/resume-screening-analysis/<时间戳>")
    analyze.add_argument("--json", action="store_true", help="把完整 JSON 打到 stdout")
    analyze.add_argument("--no-write", action="store_true", help="不落盘，只打印")
    analyze.add_argument("--markdown", action="store_true", help="把 Markdown 报告打到 stdout")
    analyze.add_argument("--overwrite", action="store_true", help="允许覆盖 --out 下已有的报告文件")
    analyze.add_argument(
        "--cache-dir",
        default="",
        help="批量分析结果缓存目录（按文件内容+岗位要求寻址，重跑/断点续跑跳过 OCR）",
    )
    analyze.add_argument(
        "--allow-empty-requirements",
        action="store_true",
        help="岗位要求为空时仍出报告（默认拒绝，避免「0 条要求 → 0.0%% 低匹配」）",
    )

    req = sub.add_parser("requirements", help="只输出岗位三维度要求清单")
    req.add_argument("--job", "-j", default="", help="岗位名称（命中政策画像）")
    req.add_argument("--jd-file", default="", help="JD 文本文件")
    req.add_argument("--profile-id", default="", help="画像 ID")
    req.add_argument("--policy", default="", help="政策 JSON 路径")
    req.add_argument("--json", action="store_true", help="输出 JSON（默认 Markdown 表格）")

    jd = sub.add_parser("jd", help="从招聘需求字段生成结构化 JD")
    jd.add_argument("--fields-file", required=True, help="招聘需求字段 JSON 文件")
    jd.add_argument("--request-id", default="LOCAL", help="招聘工单 ID（生成 JD-<id>，默认 LOCAL）")
    jd.add_argument("--profile-id", default="", help="标准画像 ID（继承画像描述与能力/经验要求）")
    jd.add_argument("--profile-file", default="", help="画像原始 JSON 文件（优先于 --profile-id）")
    jd.add_argument("--related-jd-title", default="", help="相关 JD 模板检索词（只作措辞参考；不传则按岗位名称自动匹配）")
    jd.add_argument("--library-file", default="", help="JD 模板库 JSON 路径（默认 data/generated/jd/jd_library.json）")
    jd.add_argument("--out", default="", help="输出目录；不传则写入 workspace/runs/resume-screening-jd/<时间戳>")
    jd.add_argument("--json", action="store_true", help="把完整 JD JSON 打到 stdout")
    jd.add_argument("--markdown", action="store_true", help="把发布的 JD 文档（岗位职责 + 任职要求）打到 stdout（默认在非 --json 时也打印）")
    jd.add_argument("--summary", action="store_true", help="只打印聊天摘要（JD 文档写入 jd.md）")
    jd.add_argument("--external", action="store_true", help="对外发布版：去公司标识、省略内部部门/岗位定位/模板编号")
    jd.add_argument("--draft", action="store_true", help="先用 LLM 按对外发布 JD 撰写规范起草职责与要求（需配置 RECRUITER_LLM_*，失败自动回退）")
    jd.add_argument("--check", action="store_true", help="按撰写规范自检并写 jd.quality.md；有 ERROR 时退出码 4")
    jd.add_argument("--tags", action="store_true", help="依据 JD 拆出评分标签（能力/经验/特质 + 硬性条件）并写 jd.tags.md")
    jd.add_argument("--presentation", action="store_true",
                    help="合成「最后呈现」三段（岗位职责 / 任职要求 / 匹配标签）并写 jd.presentation.md")
    jd.add_argument("--confirm-by", default="", help="记录用人部门确认人（写 jd.confirmation.json，可追溯发布前的确认）")
    jd.add_argument("--confirm-note", default="", help="确认备注（如「已与用人部门核对职责第 3 条」）")
    jd.add_argument("--no-write", action="store_true", help="不落盘，只打印")
    jd.add_argument("--overwrite", action="store_true", help="允许覆盖 --out 下已有的 jd.json / jd.md")
    return parser


def _requirements_doc(args: argparse.Namespace) -> dict[str, Any]:
    jd_text = _read_text_file(args.jd_file) if args.jd_file else ""
    return requirements_mod.build_job_requirements(
        job=args.job,
        job_text=jd_text,
        policy=args.policy or None,
        profile_id=args.profile_id,
    )


def _cmd_requirements(args: argparse.Namespace) -> int:
    try:
        doc = _requirements_doc(args)
    except (OSError, ValueError) as exc:
        print(f"输入错误：{exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    if args.json:
        _dump(doc)
    else:
        print(requirements_mod.render_requirements_markdown(doc))
        excluded = doc.get("excluded") or []
        if excluded:
            print("### 该画像下不适用的要求")
            print("")
            for row in excluded:
                print(f"- {row.get('name')}：{row.get('reason')}")
            print("")
    for warning in doc.get("warnings") or []:
        print(f"提示：{warning}", file=sys.stderr)
    return EXIT_OK


def _write_single(report: dict[str, Any], args: argparse.Namespace) -> int:
    try:
        paths = service.write_report(report, out_dir=args.out or None, stem="analysis", overwrite=args.overwrite)
    except FileExistsError as exc:
        print(f"写盘失败：{exc}", file=sys.stderr)
        return EXIT_ANALYSIS_ERROR
    except OSError as exc:
        print(f"写盘失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ANALYSIS_ERROR
    print(f"\n已写入：{paths['markdown']}", file=sys.stderr)
    return EXIT_OK


def _requirements_from_jd_source(args: argparse.Namespace) -> dict[str, Any] | None:
    """Build the requirements doc from ``--fields-file`` / ``--jd-json`` (None when neither given)."""
    if not args.jd_json and not args.fields_file:
        return None
    if args.jd_json:
        jd_doc = _read_json_file(args.jd_json)
    else:
        jd_doc = _build_jd(args)
    if not isinstance(jd_doc, dict):
        raise ValueError("JD 文件必须是 JSON 对象")
    return requirements_mod.requirements_from_jd(jd_doc)


def _cmd_analyze(args: argparse.Namespace) -> int:
    if args.text and args.files:
        print("--text 与简历文件不能同时使用：请二选一。", file=sys.stderr)
        return EXIT_INPUT_ERROR
    if args.fields_file and args.jd_json:
        print("--fields-file 与 --jd-json 不能同时使用：岗位要求来源请二选一。", file=sys.stderr)
        return EXIT_INPUT_ERROR
    if (args.fields_file or args.jd_json) and (args.jd_file or args.job):
        print(
            "--fields-file/--jd-json 与 --jd-file/--job 不能同时使用：岗位要求来源请只选一种。",
            file=sys.stderr,
        )
        return EXIT_INPUT_ERROR
    try:
        jd_text = _read_text_file(args.jd_file) if args.jd_file else ""
        requirements_doc = _requirements_from_jd_source(args)
    except (OSError, ValueError) as exc:
        print(f"输入错误：{exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    # 岗位要求是匹配的分母：默认严格模式，空要求直接拒绝出报告。
    common: dict[str, Any] = {
        "job": args.job,
        "job_text": jd_text,
        "policy": args.policy or None,
        "profile_id": args.profile_id,
        "requirements_doc": requirements_doc,
        "strict_requirements": not args.allow_empty_requirements,
    }

    if args.text:
        try:
            report = service.analyze_text(args.text, filename="inline.txt", **common)
        except requirements_mod.RequirementsUnavailableError as exc:
            print(f"岗位要求为空：{exc}", file=sys.stderr)
            return EXIT_INPUT_ERROR
        if args.json:
            _dump(report)
        if args.markdown or not args.json:
            print(service.render_report_markdown(report))
        if args.no_write:
            return EXIT_OK
        return _write_single(report, args)

    if not args.files:
        print("请提供简历文件，或使用 --text 直接传文本。", file=sys.stderr)
        return EXIT_INPUT_ERROR

    if len(args.files) == 1:
        try:
            report = service.analyze_resume_file(args.files[0], **common)
        except requirements_mod.RequirementsUnavailableError as exc:
            print(f"岗位要求为空：{exc}", file=sys.stderr)
            return EXIT_INPUT_ERROR
        except FileNotFoundError:
            print(f"输入错误：找不到简历文件 {Path(args.files[0]).name}", file=sys.stderr)
            return EXIT_INPUT_ERROR
        except ValueError as exc:  # 编码/格式无法解析
            print(f"输入错误：{exc}", file=sys.stderr)
            return EXIT_INPUT_ERROR
        except Exception as exc:  # noqa: BLE001 - 命令行边界，最终兜底
            print(f"分析失败：{service.sanitize_error(exc)}", file=sys.stderr)
            return EXIT_ANALYSIS_ERROR
        if args.json:
            _dump(report)
        if args.markdown or not args.json:
            print(service.render_report_markdown(report))
        if args.no_write:
            return EXIT_OK
        return _write_single(report, args)

    try:
        batch = service.analyze_resume_files(args.files, cache_dir=args.cache_dir or None, **common)
    except requirements_mod.RequirementsUnavailableError as exc:
        print(f"岗位要求为空：{exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    if args.json:
        _dump(batch)
    else:
        print(f"# 批量简历匹配（{batch.get('job') or '未指定岗位'}）")
        if batch.get("jd_id"):
            print("")
            print(f"- 岗位要求来源：生成 JD {batch['jd_id']}（标签命中率排序）")
        print("")
        print("先看命中了几项，再看还剩几项待验证（面试/证件核验时确认，没写不等于不会）。")
        print("")
        print("| 排名 | 姓名 | 文件 | 命中标签 | 待验证项 | 未命中（反证） | 结论（推荐/待核实/不推荐）|")
        print("| --- | --- | --- | --- | --- | --- | --- |")
        for row in batch.get("ranking") or []:
            pct = matching_mod.percent_text
            hit = row.get("tag_hit_count") or 0
            applied = row.get("tag_applied_count") or 0
            pending = row.get("pending_verification") or []
            pending_text = f"{len(pending)} 项" + ("：" + "、".join(pending) if pending else "")
            print(
                f"| {row.get('rank')} | {row.get('name','')} | {row.get('filename','')} "
                f"| {hit}/{applied}（{pct(row.get('overall_percent'))}） | {pending_text} "
                f"| {row.get('tag_contradicted_count') or 0} 项 | {row.get('recommendation') or '—'} |"
            )
        if batch.get("errors"):
            print("")
            for error in batch["errors"]:
                print(f"- 失败：{error.get('path')}（{error.get('error')}）")
    failed = len(batch.get("errors") or [])
    succeeded = len(batch.get("reports") or [])
    if succeeded and not args.no_write:
        try:
            paths = service.write_batch_report(batch, out_dir=args.out or None, overwrite=args.overwrite)
        except FileExistsError as exc:
            print(f"写盘失败：{exc}", file=sys.stderr)
            return EXIT_ANALYSIS_ERROR
        except OSError as exc:
            print(f"写盘失败：{type(exc).__name__}: {exc}", file=sys.stderr)
            return EXIT_ANALYSIS_ERROR
        print(f"\n已写入：{paths['markdown']}", file=sys.stderr)
    if not succeeded:
        print(f"全部 {failed} 份简历都解析/分析失败，未生成报告。", file=sys.stderr)
        return EXIT_ANALYSIS_ERROR
    if failed:
        print(f"提示：{failed} 份简历失败，其余 {succeeded} 份已分析。", file=sys.stderr)
    return EXIT_OK


def _read_json_file(path: str) -> Any:
    try:
        return json.loads(_read_text_file(path))
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 解析失败：{Path(path).name}（{exc.msg}）") from exc


def _build_jd(args: argparse.Namespace) -> dict[str, Any]:
    fields_doc = _read_json_file(args.fields_file)
    if not isinstance(fields_doc, dict):
        raise ValueError("需求字段文件必须是 JSON 对象")
    request_id = str(args.request_id or "LOCAL")
    if isinstance(fields_doc.get("fields"), dict):
        request_id = str(fields_doc.get("request_id") or request_id)
        fields = fields_doc["fields"]
    else:
        fields = fields_doc

    profile: dict[str, Any] | None = None
    if args.profile_file:
        profile_doc = _read_json_file(args.profile_file)
        if not isinstance(profile_doc, dict):
            raise ValueError("画像文件必须是 JSON 对象")
        profile = profile_doc["raw"] if isinstance(profile_doc.get("raw"), dict) else profile_doc
    elif args.profile_id:
        from rn.repositories import ProfileRepository

        record = ProfileRepository().get(args.profile_id)
        if record is None:
            raise ValueError(f"画像不存在：{args.profile_id}")
        profile = record.raw

    related = None
    from rn.resume_screening.jd.templates import JDRepository, is_related_title

    try:
        repository = JDRepository(args.library_file or None)
        related_query = str(args.related_jd_title or "").strip()
        if related_query:
            hits = repository.search_exact(related_query) or repository.search_related(related_query)
            related = hits[0] if hits else None
            if related is None:
                print(
                    f"提示：未找到与「{related_query}」相关的 JD 模板，将只按需求字段与画像生成。",
                    file=sys.stderr,
                )
        else:
            # 需求没给模板检索词时按岗位名称找同岗位模板：职责缺失时可用它的「参考措辞（需确认）」。
            title_hint = str(fields.get("position_title") or (profile or {}).get("name") or "").strip()
            if title_hint:
                hits = repository.search_exact(title_hint) or repository.search_related(title_hint)
                if hits and is_related_title(title_hint, hits[0].title):
                    related = hits[0]
                    print(
                        f"提示：按岗位名称匹配到同岗位 JD 模板「{related.title}」（{related.jd_id}，仅作措辞参考）。",
                        file=sys.stderr,
                    )
                elif hits:
                    print(
                        f"提示：未找到与「{title_hint}」足够相近的同岗位 JD 模板，将只按需求字段与画像生成。",
                        file=sys.stderr,
                    )
    except (FileNotFoundError, ValueError) as exc:
        print(f"提示：JD 模板库不可用（{exc}），将只按需求字段与画像生成。", file=sys.stderr)
    if getattr(args, "draft", False):
        from rn.resume_screening.jd import draft as draft_mod

        try:
            return draft_mod.draft_request_jd(request_id, fields, profile, related)
        except draft_mod.JdDraftUnavailable as exc:
            print(f"提示：LLM 起草 JD 不可用（{exc}），改为按需求字段确定性生成。", file=sys.stderr)
    return jd_generator.build_request_jd(request_id, fields, profile, related)


def _write_jd_files(jd: dict[str, Any], out_dir: Path, *, overwrite: bool,
                    external: bool = False, quality_markdown: str | None = None,
                    tags_markdown: str | None = None,
                    presentation_markdown: str | None = None,
                    confirmation: dict[str, Any] | None = None) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "jd.json"
    md_path = out_dir / "jd.md"
    quality_path = out_dir / "jd.quality.md" if quality_markdown else None
    tags_path = out_dir / "jd.tags.md" if tags_markdown else None
    presentation_path = out_dir / "jd.presentation.md" if presentation_markdown else None
    confirmation_path = out_dir / "jd.confirmation.json" if confirmation else None
    if not overwrite:
        existing = [path for path in (json_path, md_path, quality_path, tags_path,
                                      presentation_path, confirmation_path)
                    if path and path.exists()]
        if existing:
            raise FileExistsError(f"JD 文件已存在：{existing[0].name}；如需覆盖请传 --overwrite")
    json_path.write_text(json.dumps(jd, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(jd_generator.render_jd_document(jd, external=external), encoding="utf-8")
    written = {"json": str(json_path), "markdown": str(md_path)}
    if quality_path is not None and quality_markdown is not None:
        quality_path.write_text(quality_markdown.rstrip() + "\n", encoding="utf-8")
        written["quality"] = str(quality_path)
    if tags_path is not None and tags_markdown is not None:
        tags_path.write_text(tags_markdown.rstrip() + "\n", encoding="utf-8")
        written["tags"] = str(tags_path)
    if presentation_path is not None and presentation_markdown is not None:
        presentation_path.write_text(presentation_markdown.rstrip() + "\n", encoding="utf-8")
        written["presentation"] = str(presentation_path)
    if confirmation_path is not None and confirmation is not None:
        confirmation_path.write_text(json.dumps(confirmation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written["confirmation"] = str(confirmation_path)
    return written


def _cmd_jd(args: argparse.Namespace) -> int:
    try:
        jd = _build_jd(args)
    except FileNotFoundError:
        print(f"输入错误：找不到文件 {args.fields_file}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    except (OSError, ValueError) as exc:
        print(f"输入错误：{exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    external = bool(getattr(args, "external", False))
    document = jd_generator.render_jd_document(jd, external=external)
    tags_markdown = None
    if getattr(args, "tags", False):
        tags_doc = requirements_mod.requirements_from_jd(jd)
        tags_markdown = requirements_mod.render_requirements_markdown(tags_doc)
    presentation_markdown = None
    if getattr(args, "presentation", False):
        from rn.resume_screening.jd import presentation as presentation_mod

        presentation_markdown = presentation_mod.render_presentation(jd, external=external)
    quality_markdown = None
    quality_ok = True
    confirmation: dict[str, Any] | None = None
    confirm_by = str(getattr(args, "confirm_by", "") or "").strip()
    if confirm_by:
        confirmation = {
            "schema_version": "jd-confirmation/1.0",
            "jd_id": jd.get("jd_id") or "",
            "confirmed_by": confirm_by,
            "confirmed_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "note": str(getattr(args, "confirm_note", "") or "").strip(),
            "external": external,
        }
    if getattr(args, "check", False):
        from rn.resume_screening.jd import quality as quality_mod

        report = quality_mod.validate_jd(jd, document=document, external=external)
        quality_markdown = quality_mod.render_quality_report(report, external=external)
        if confirmation:
            quality_markdown += (
                f"\n发布确认：已确认（{confirmation['confirmed_by']}，{confirmation['confirmed_at']}）"
                + (f"，备注：{confirmation['note']}" if confirmation["note"] else "")
                + "\n"
            )
        else:
            quality_markdown += "\n发布确认：待用人部门确认（确认后重跑并传 --confirm-by 记录）\n"
        quality_ok = report.ok
        print(quality_markdown)
        print()

    if args.json:
        _dump(jd)
    if args.summary:
        print(jd_generator.render_jd_summary(jd))
    elif presentation_markdown is not None:
        # 「最后呈现」= 三个大项；对外发布正文仍在 jd.md 里保持两节。
        print(presentation_markdown)
    elif args.markdown or not args.json:
        print(document)
    if tags_markdown and not args.json and presentation_markdown is None:
        print()
        print("---")
        print()
        print(tags_markdown)
    if args.no_write:
        return EXIT_OK if quality_ok else EXIT_QUALITY_ERROR
    out_dir = Path(args.out) if args.out else paths.JD_RUN_ROOT / datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        written = _write_jd_files(
            jd, out_dir, overwrite=args.overwrite, external=external,
            quality_markdown=quality_markdown, tags_markdown=tags_markdown,
            presentation_markdown=presentation_markdown, confirmation=confirmation,
        )
    except FileExistsError as exc:
        print(f"写盘失败：{exc}", file=sys.stderr)
        return EXIT_ANALYSIS_ERROR
    except OSError as exc:
        print(f"写盘失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ANALYSIS_ERROR
    print(f"\n已写入：{written['markdown']}", file=sys.stderr)
    if written.get("quality"):
        print(f"已写入：{written['quality']}", file=sys.stderr)
    if written.get("tags"):
        print(f"已写入：{written['tags']}", file=sys.stderr)
    if written.get("presentation"):
        print(f"已写入：{written['presentation']}", file=sys.stderr)
    if written.get("confirmation"):
        print(f"已写入：{written['confirmation']}", file=sys.stderr)
    if not quality_ok:
        print("自检未通过：按上面的 ERROR 清单修改后再发布。", file=sys.stderr)
        return EXIT_QUALITY_ERROR
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "requirements":
        return _cmd_requirements(args)
    if args.command == "analyze":
        return _cmd_analyze(args)
    if args.command == "jd":
        return _cmd_jd(args)
    parser.print_help()
    return EXIT_INPUT_ERROR


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
