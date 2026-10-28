"""Audit the tag-hit-rate matching against a folder of real resumes.

This is an evaluation helper, not part of the product path. It runs the packaged
``service.analyze_resume_file`` with a generated JD and writes:

* ``audit.json`` — machine-readable per resume / per tag results
* ``audit.md`` — a matrix (tags × resumes) plus the evidence behind every
  matched / weak tag, so precision can be checked by hand
* ``expected-score.md`` — agreement against ``expected.json`` when human labels
  are filled in (initially empty, so it reports "not labelled yet")

Usage (from the repo root, Windows):

    python scripts\\eval-matching.py --jd-json workspace\\runs\\jd-presentation\\jd.json ^
        --resumes workspace\\test_resumes\\inspection_technician ^
        --out workspace\\runs\\matching-accuracy
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "runtime"))

from rn.resume_screening import service  # noqa: E402
from rn.resume_screening.analysis import requirements as requirements_mod  # noqa: E402

STATUS_MARK = {
    "matched": "命中",
    "weak": "待验证",
    "not_found": "待验证（未提及）",
    "contradicted": "未命中（反证）",
    "interview_pending": "待面试核",
}
# 系统「匹配档位」→ 校验表里的三档结论（用于人工一致率对照）
SYSTEM_VERDICT = {
    "高匹配": "推荐",
    "较高匹配": "推荐",
    "中等匹配": "待核实",
    "偏低匹配": "不推荐",
    "低匹配": "不推荐",
}


def _analyse(jd: dict, pdf: Path) -> dict:
    doc = requirements_mod.requirements_from_jd(jd)
    report = service.analyze_resume_file(str(pdf), requirements_doc=doc)
    matching = report["matching"]
    judged = {row["requirement_id"]: row for row in doc.get("requirements") or []}
    items = []
    for dimension in ("capability", "experience"):
        for item in matching["dimensions"][dimension]["items"]:
            requirement = judged.get(item["requirement_id"], {})
            items.append({
                "requirement_id": item["requirement_id"],
                "dimension": dimension,
                "name": item["name"],
                "level": item["level"],
                "state": item["state"],
                # 判定口径随标签一起写进人工校验表的表头，填表人不用再翻文档
                "judge_mode": requirement.get("decision_mode") or "",
                "judge_keywords": requirement.get("keywords") or [],
                "judge_strong_keywords": requirement.get("strong_keywords") or [],
                "matched_keywords": item.get("matched_keywords") or [],
                "strong_keywords": item.get("strong_keywords") or [],
                "evidence": item.get("evidence") or "",
            })
    return {
        "file": pdf.name,
        "tag_hit_count": matching["tag_hit_count"],
        "tag_applied_count": matching["tag_applied_count"],
        "tag_weak_count": matching["tag_weak_count"],
        "tag_missing_count": matching["tag_missing_count"],
        "hit_percent": matching["tag_hit_percent"],
        "label": matching["label"],
        "recommendation": matching["recommendation"],
        "disqualified": matching["disqualified"],
        "hard_filters": [
            {
                "filter": hint["filter"],
                "status": hint["status"],
                "evidence": hint.get("evidence") or "",
                "note": hint.get("note") or "",
            }
            for hint in matching["hard_filter_hints"]
        ],
        "items": items,
    }


def _load_expected(resumes: Path) -> dict:
    path = resumes / "expected.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_matrix(rows: list[dict]) -> str:
    lines: list[str] = ["# 匹配准确度审计（标签 × 简历）", ""]
    lines.append("| 简历 | 命中/适用 | 命中率 | 待验证（有线索/未提及） | 未命中（有反证） | 结论 | 硬条件 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for row in rows:
        hard = "、".join(
            f"{hint['filter']}={hint['status']}" for hint in row["hard_filters"]
        )
        lines.append(
            f"| {row['file']} | {row['tag_hit_count']}/{row['tag_applied_count']} "
            f"| {row['hit_percent']}% | {row['tag_weak_count'] + row['tag_missing_count']} "
            f"| {row.get('tag_contradicted_count', 0)} "
            f"| {row['label']} | {hard} |"
        )
    lines.append("")

    tag_ids = [item["requirement_id"] for item in rows[0]["items"]]
    tag_names = {item["requirement_id"]: f"{item['name']}（{item['dimension']}）" for item in rows[0]["items"]}
    lines.append("## 标签矩阵")
    lines.append("")
    lines.append("| 编号 | 标签 | " + " | ".join(row["file"].split("_")[0] for row in rows) + " |")
    lines.append("| --- | --- | " + " | ".join("---" for _ in rows) + " |")
    for tag_id in tag_ids:
        cells = []
        for row in rows:
            item = next(item for item in row["items"] if item["requirement_id"] == tag_id)
            cells.append(STATUS_MARK.get(item["state"], item["state"]))
        lines.append(f"| {tag_id} | {tag_names[tag_id]} | " + " | ".join(cells) + " |")
    lines.append("")
    return "\n".join(lines)


def _write_evidence(rows: list[dict]) -> str:
    lines: list[str] = ["## 证据抽查（命中 / 待核实）", ""]
    for row in rows:
        lines.append(f"### {row['file']}")
        lines.append("")
        for item in row["items"]:
            if item["state"] not in {"matched", "weak"}:
                continue
            keywords = "、".join(item["matched_keywords"]) or "—"
            strong = "、".join(item["strong_keywords"]) or "—"
            lines.append(
                f"- {item['requirement_id']} {item['name']}：{STATUS_MARK.get(item['state'])}"
                f"；命中词 {keywords}；强词 {strong}"
            )
            if item["evidence"]:
                lines.append(f"  - 证据：{item['evidence']}")
        lines.append("")
    return "\n".join(lines)


def _expected_section(rows: list[dict], expected: dict) -> str:
    labels = [row for row in (expected.get("rows") or []) if row.get("recommendation")]
    lines: list[str] = ["## 人工标注对照", ""]
    if not labels:
        lines.append("expected.json 还没有填写人工结论（hard_conditions / recommendation 全空），"
                     "所以无法计算人工一致率；这里先给出规则侧分布，供标注时参考：")
        lines.append("")
        return "\n".join(lines)
    by_file = {row["file"]: row for row in rows}
    agree = 0
    lines.append("| 简历 | 人工结论 | 规则结论 | 一致 |")
    lines.append("| --- | --- | --- | --- |")
    for label in labels:
        rule = by_file.get(label["name"])
        if not rule:
            continue
        same = label["recommendation"] == rule["recommendation"]
        agree += int(same)
        lines.append(f"| {label['name']} | {label['recommendation']} | {rule['recommendation']} | {'是' if same else '否'} |")
    lines.append("")
    lines.append(f"一致率：{agree}/{len(labels)} = {agree / max(len(labels), 1):.0%}")
    return "\n".join(lines)


def _review_section(rows: list[dict], path: Path) -> str:
    """Agreement between the rule conclusion and the filled 人工校验表.xlsx."""
    from openpyxl import load_workbook

    if not path.exists():
        return f"## 人工校验\n\n未找到 {path}，先运行 scripts\\make-review-sheet.py 生成表格。"
    workbook = load_workbook(path, data_only=True)
    sheet = workbook[workbook.sheetnames[0]]
    header = [str(cell.value or "") for cell in sheet[1]]
    try:
        file_col = header.index("简历文件")
        verdict_col = header.index("人工结论")
    except ValueError:
        return "## 人工校验\n\n表格缺少「简历文件」或「人工结论」列。"
    by_file = {row["file"]: row for row in rows}
    agree = total = 0
    lines = ["## 人工校验一致率", "", "| 简历 | 人工结论 | 系统结论 | 一致 |", "| --- | --- | --- | --- |"]
    for values in sheet.iter_rows(min_row=2, values_only=True):
        if not values or values[file_col] is None or values[verdict_col] in (None, ""):
            continue
        file_name = str(values[file_col])
        human = str(values[verdict_col]).strip()
        rule = by_file.get(file_name)
        if not rule:
            continue
        # 以报告的 recommendation 为准（命中率 < 40% 是「待核实」，不推荐只留给硬条件不满足）；
        # 旧 audit.json 没有该字段时退回档位映射。
        system = rule.get("recommendation") or SYSTEM_VERDICT.get(rule["label"], rule["label"])
        same = human == system
        agree += int(same)
        total += 1
        lines.append(f"| {file_name} | {human} | {system} | {'是' if same else '否'} |")
    lines.append("")
    if total == 0:
        lines.append("人工结论列还没有填写。")
    else:
        lines.append(f"一致率：{agree}/{total} = {agree / total:.0%}")

    cell_lines = _cell_agreement(sheet, header, rows)
    if cell_lines:
        lines.append("")
        lines.extend(cell_lines)
    return "\n".join(lines)


def _norm_mark(text: str) -> str:
    """Normalize a human/system tag mark onto the three user-facing buckets.

    新口径：没写 = 待验证（未提及），只有反证才是未命中。人工表里历史填过的
    「未命中」按「简历没写」处理，否则同一件事会因为改文案而算不一致。
    """
    match = re.match(r"^(待验证（未提及）|待验证（有线索）|待验证|命中|待核实|未命中)", str(text or "").strip())
    if not match:
        return ""
    mark = match.group(1)
    if mark in {"待核实", "待验证"}:
        return "待验证（有线索）"
    if mark == "未命中":
        return "待验证（未提及）"
    return mark


def _cell_agreement(sheet, header: list[str], rows: list[dict]) -> list[str]:
    """Per-tag agreement for the tag columns of the review sheet (red corrections included)."""
    tag_columns = [index for index, text in enumerate(header) if "判据：" in text]
    if not tag_columns:
        return []
    try:
        file_col = header.index("简历文件")
    except ValueError:
        return []
    by_file = {row["file"]: row for row in rows}
    agree = total = 0
    differences: list[str] = []
    for values in sheet.iter_rows(min_row=2, values_only=True):
        if not values or values[file_col] is None:
            continue
        rule = by_file.get(str(values[file_col]))
        if not rule:
            continue
        states = {item["name"]: _norm_mark(STATUS_MARK.get(item["state"], "")) for item in rule["items"]}
        for index in tag_columns:
            name = header[index].splitlines()[0].split("（")[0]
            human = _norm_mark(str(values[index] or ""))
            system = states.get(name, "")
            if not human:
                continue
            total += 1
            if human == system:
                agree += 1
            else:
                differences.append(f"| {values[file_col]} | {name} | {system} | {human} |")
    if not total:
        return []
    lines = [
        "### 标签单元一致率",
        "",
        f"标签判定一致：{agree}/{total} = {agree / total:.1%}",
    ]
    if differences:
        lines.extend([
            "",
            "| 简历 | 标签 | 系统 | 人工 |",
            "| --- | --- | --- | --- |",
            *differences,
        ])
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit tag-hit-rate matching on real resumes")
    parser.add_argument("--jd-json", required=True)
    parser.add_argument("--resumes", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--review-xlsx", default="", help="人工校验表.xlsx，填好人工结论后用来算一致率")
    args = parser.parse_args(argv)

    jd = json.loads(Path(args.jd_json).read_text(encoding="utf-8"))
    resume_dir = Path(args.resumes)
    pdfs = sorted(resume_dir.glob("*.pdf"))
    if not pdfs:
        parser.error(f"no PDF resumes under {resume_dir}")

    rows = []
    for pdf in pdfs:
        print(f"analyzing {pdf.name} ...", file=sys.stderr)
        rows.append(_analyse(jd, pdf))

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "audit.json").write_text(
        json.dumps({"jd": str(args.jd_json), "rows": rows}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    expected = _load_expected(resume_dir)
    sections = [
        _write_matrix(rows),
        _write_evidence(rows),
        _expected_section(rows, expected),
    ]
    if args.review_xlsx:
        sections.append(_review_section(rows, Path(args.review_xlsx)))
    markdown = "\n\n".join(sections)
    (out / "audit.md").write_text(markdown, encoding="utf-8")

    counts = Counter(row["label"] for row in rows)
    print(json.dumps({
        "resumes": len(rows),
        "labels": dict(counts),
        "hit_percent": [row["hit_percent"] for row in rows],
        "out": str(out),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
