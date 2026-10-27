#!/usr/bin/env python3
"""对测试样本目录里的简历跑一遍解析+评分，产出 baseline.json / baseline.md。

用途：给人工标注「推荐/待核实/不推荐」提供同一口径的参考快照；以后改了解析/评分规则，
       重跑本脚本即可对比差异，判断是回归还是预期变化。

用法：
  python scripts/baseline-test-resumes.py --dir workspace/test_resumes/inspection_technician --job 检验技术员
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from rn.resume_screening import service  # noqa: E402

RESUME_SUFFIXES = {".pdf", ".docx", ".doc", ".jpg", ".jpeg", ".png", ".bmp", ".webp"}
NON_RESUME_STEMS = {"README", "baseline", "expected", "manifest"}


def _is_resume_file(path: Path) -> bool:
    if path.suffix.lower() not in RESUME_SUFFIXES:
        return False
    return path.stem not in NON_RESUME_STEMS


def field(report: dict, key: str):
    value = (report.get("fields") or {}).get(key)
    if isinstance(value, dict):
        return value.get("value")
    return value


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="测试样本目录")
    ap.add_argument("--job", default="检验技术员", help="岗位名（决定用哪套岗位要求）")
    ap.add_argument("--out", default="", help="baseline.json 输出路径（默认写样本目录下）")
    args = ap.parse_args()

    sample_dir = (ROOT / args.dir).resolve() if not Path(args.dir).is_absolute() else Path(args.dir)
    if not sample_dir.is_dir():
        print(f"目录不存在: {sample_dir}", file=sys.stderr)
        return 2
    out_path = Path(args.out) if args.out else sample_dir / "baseline.json"

    files = sorted(p for p in sample_dir.iterdir() if p.is_file() and _is_resume_file(p))
    if not files:
        print(f"目录下没有简历文件: {sample_dir}", file=sys.stderr)
        return 2

    rows = []
    for path in files:
        try:
            report = service.analyze_resume_file(path, job=args.job)
        except Exception as exc:  # 单份失败不阻断整批
            rows.append({"name": path.name, "error": f"{type(exc).__name__}: {exc}"})
            print(f"{path.name}: ERROR {type(exc).__name__}: {exc}")
            continue
        doc = report.get("document") or {}
        edu = (report.get("fields") or {}).get("education") or {}
        match = report.get("matching") or {}
        row = {
            "name": path.name,
            "parse": {
                "engines": doc.get("engines") or [],
                "pages": doc.get("page_count"),
                "text_chars": doc.get("text_chars"),
                "ocr_pages": (doc.get("meta") or {}).get("ocr_pages") or [],
                "warnings": doc.get("warnings") or [],
            },
            "fields": {
                "name": field(report, "name"),
                "phone": field(report, "phone"),
                "email": field(report, "email"),
                "gender": field(report, "gender"),
                "age": field(report, "age"),
                "education_level": edu.get("level"),
                "school": edu.get("school"),
                "major": edu.get("major"),
                "total_work_years": field(report, "total_work_years"),
                "expected_position": field(report, "expected_position"),
                "certifications": (report.get("fields") or {}).get("certifications") or [],
            },
            "matching": {
                "overall_percent": match.get("overall_percent"),
                "label": match.get("label"),
                "tag_applied_count": match.get("tag_applied_count"),
                "tag_hit_count": match.get("tag_hit_count"),
                "disqualified": match.get("disqualified"),
                "disqualification_reason": match.get("disqualification_reason"),
                "recommendation": match.get("recommendation"),
            },
        }
        rows.append(row)
        print(f"{path.name}: {row['matching']['overall_percent']}% {row['matching']['label']} "
              f"(engines={','.join(row['parse']['engines'])})")

    payload = {
        "schema_version": 1,
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "job": args.job,
        "sample_dir": str(sample_dir.relative_to(ROOT)) if sample_dir.is_relative_to(ROOT) else str(sample_dir),
        "note": "baseline 是当前解析/评分规则的快照，只供人工对照，不是录用结论。",
        "rows": rows,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    md_path = out_path.with_suffix(".md")
    lines = [
        f"# 测试样本解析基线（岗位：{args.job}）",
        "",
        f"生成时间：{payload['generated_at']}",
        "",
        "| 简历 | 解析引擎 | 姓名 | 学历 | 院校 | 专业 | 工作年限 | 匹配度 | 标签命中 | 结论 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        if "error" in row:
            lines.append(f"| {row['name']} | 解析失败 | - | - | - | - | - | - | - | {row['error']} |")
            continue
        f, m, p = row["fields"], row["matching"], row["parse"]
        engines = "/".join(e for e in p["engines"] if e)
        lines.append(
            "| {name} | {engines} | {cn} | {edu} | {school} | {major} | {years} | {pct}% | {hit}/{applied} | {label} |".format(
                name=row["name"], engines=engines or "-",
                cn=f["name"] or "-", edu=f["education_level"] or "-",
                school=f["school"] or "-", major=f["major"] or "-",
                years=f["total_work_years"] if f["total_work_years"] is not None else "-",
                pct=m["overall_percent"], hit=m["tag_hit_count"], applied=m["tag_applied_count"],
                label=m["label"] or "-",
            )
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n共 {len(rows)} 份；baseline: {out_path}\nmarkdown: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
