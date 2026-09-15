#!/usr/bin/env python3
"""Rebuild Recruiter-Na data foundation from immutable source assets.

Authoritative inputs:
- data/source/profiles/originals/*.pdf (standard profile source PDFs)
- data/source/jd/岗位清单及JD库.xlsx (JD source workbook)
- data/source/resumes/private/** (private resume source pack; NOT production authority)

Generated outputs are deterministic aside from generated_at timestamps in manifests.
Production resume authority remains MySQL and is never rebuilt from this source pack automatically.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
PROFILE_SOURCE_DIR = ROOT / "data/source/profiles/originals"
JD_SOURCE = ROOT / "data/source/jd/岗位清单及JD库.xlsx"
RESUME_SOURCE_DIR = ROOT / "data/source/resumes/private"
PROFILE_OUT_DIR = ROOT / "data/generated/profiles"
JD_OUT_DIR = ROOT / "data/generated/jd"
MANIFEST_DIR = ROOT / "data/manifests"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def stable_profile_id(name: str) -> str:
    return "PROFILE-" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:12].upper()


def _section(text: str, start: str, end: str | None = None) -> str:
    i = text.find(start)
    if i < 0:
        return ""
    i += len(start)
    j = text.find(end, i) if end else len(text)
    if j < 0:
        j = len(text)
    return text[i:j]


def _join_lines(block: str) -> str:
    lines = []
    for line in block.replace("\f", "\n").splitlines():
        line = line.strip()
        if not line or re.fullmatch(r"\d+\s*/\s*\d+", line):
            continue
        lines.append(line)
    return "".join(lines)


def _numbered_items(block: str) -> list[dict]:
    lines = [x.strip() for x in block.splitlines() if x.strip()]
    out: list[dict] = []
    current: dict | None = None
    for line in lines:
        m = re.match(r"^(\d+)\s+([^：:]+)[：:](.*)$", line)
        if m:
            if current:
                out.append(current)
            current = {
                "name": m.group(2).strip(),
                "def": m.group(3).strip(),
                "required": True,
            }
        elif current:
            current["def"] += line
    if current:
        out.append(current)
    return out


def parse_profile_pdf(path: Path) -> tuple[dict, str]:
    raw_text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    raw_text = raw_text.replace("\f", "\n")
    challenge = _join_lines(_section(raw_text, "业务特征/挑战：", "【关键能力要求】"))
    abilities = _numbered_items(_section(raw_text, "【关键能力要求】：", "【关键经验要求】"))
    if not abilities:
        abilities = _numbered_items(_section(raw_text, "【关键能力要求】", "【关键经验要求】"))
    experiences = _numbered_items(_section(raw_text, "【关键经验要求】：", "【管理经验】"))
    if not experiences:
        experiences = _numbered_items(_section(raw_text, "【关键经验要求】", "【管理经验】"))
    management = _join_lines(_section(raw_text, "【管理经验】", "【其他特质要求】"))
    trait_text = _join_lines(_section(raw_text, "【其他特质要求】", "版本更新时间"))
    traits = []
    for part in re.split(r"[；;]", trait_text):
        m = re.match(r"([^：:]+)[：:]", part)
        if m:
            traits.append(m.group(1).strip())
    version_match = re.search(r"版本更新时间[:：]\s*([0-9.\-]+)", raw_text)
    version_date = version_match.group(1) if version_match else ""
    profile = {
        "profile_id": stable_profile_id(path.stem),
        "name": path.stem,
        "aliases": [],
        "source_status": "VERIFIED_SOURCE",
        "source_file": path.name,
        "source_sha256": sha256(path),
        "source_version_date": version_date,
        "business_challenge": challenge,
        "description": challenge,
        "management_level": 0,
        "team_size_range": "",
        "key_abilities": abilities,
        "key_experiences": experiences,
        "management": management,
        "key_traits": traits,
    }
    if not challenge or not abilities or not experiences:
        raise ValueError(f"Profile parse incomplete: {path.name}")
    return profile, raw_text


def build_profiles() -> dict:
    PROFILE_OUT_DIR.mkdir(parents=True, exist_ok=True)
    source_text_dir = PROFILE_OUT_DIR / "source_text"
    source_text_dir.mkdir(parents=True, exist_ok=True)
    sources = []
    profiles = []
    for pdf in sorted(PROFILE_SOURCE_DIR.glob("*.pdf"), key=lambda p: p.name):
        profile, raw_text = parse_profile_pdf(pdf)
        profiles.append(profile)
        txt_path = source_text_dir / f"{pdf.stem}.txt"
        txt_path.write_text(raw_text, encoding="utf-8")
        sources.append({
            "file_name": pdf.name,
            "sha256": sha256(pdf),
            "profile_id": profile["profile_id"],
            "profile_name": profile["name"],
            "source_version_date": profile["source_version_date"],
            "status": "VERIFIED_SOURCE",
        })
    library = {
        "schema_version": "3.4",
        "meta": {
            "total": len(profiles),
            "authority": "VERIFIED_SOURCE_PDF",
            "source_directory": "data/source/profiles/originals",
            "generated_from_source": True,
        },
        "profiles": profiles,
    }
    (PROFILE_OUT_DIR / "profile_library.json").write_text(
        json.dumps(library, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    md = [
        "# 标准岗位画像库",
        "",
        f"- 权威来源：`data/source/profiles/originals/` 中 {len(profiles)} 份原始 PDF",
        "- 状态：VERIFIED_SOURCE",
        "- 注意：旧 40 条混合画像已退出生产事实源。",
        "",
    ]
    for p in profiles:
        md += [
            f"## {p['name']}",
            f"- Profile ID: `{p['profile_id']}`",
            f"- 来源：`{p['source_file']}`",
            f"- 版本日期：{p['source_version_date'] or '-'}",
            f"- 业务挑战：{p['business_challenge']}",
            "- 关键能力：" + "、".join(x["name"] for x in p["key_abilities"]),
            "- 关键经验：" + "、".join(x["name"] for x in p["key_experiences"]),
            "- 特质：" + "、".join(p["key_traits"]),
            "",
        ]
    (PROFILE_OUT_DIR / "profile_library.md").write_text("\n".join(md), encoding="utf-8")
    manifest = {
        "schema_version": "1.0",
        "authority": "VERIFIED_SOURCE_PDF",
        "total_sources": len(sources),
        "sources": sources,
    }
    (MANIFEST_DIR / "profile_sources.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return library


_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_REL_DOC = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_REL_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"


def _xlsx_rows(path: Path) -> tuple[str, list[list[str]]]:
    with zipfile.ZipFile(path) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall(f"{{{_NS_MAIN}}}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{_NS_MAIN}}}t")))
        wb_root = ET.fromstring(z.read("xl/workbook.xml"))
        first_sheet = wb_root.find(f"{{{_NS_MAIN}}}sheets/{{{_NS_MAIN}}}sheet")
        if first_sheet is None:
            raise ValueError("No worksheet in JD workbook")
        sheet_name = first_sheet.attrib.get("name", "Sheet1")
        rel_id = first_sheet.attrib.get(f"{{{_NS_REL_DOC}}}id")
        rel_root = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        target = None
        for rel in rel_root.findall(f"{{{_NS_REL_PKG}}}Relationship"):
            if rel.attrib.get("Id") == rel_id:
                target = rel.attrib.get("Target")
                break
        if not target:
            raise ValueError("Cannot resolve first worksheet")
        sheet_path = "xl/" + target.lstrip("/") if not target.startswith("xl/") else target
        sheet_root = ET.fromstring(z.read(sheet_path))
        rows: list[list[str]] = []
        for row in sheet_root.findall(f".//{{{_NS_MAIN}}}row"):
            values: dict[int, str] = {}
            for c in row.findall(f"{{{_NS_MAIN}}}c"):
                ref = c.attrib.get("r", "A1")
                letters = re.match(r"([A-Z]+)", ref)
                if not letters:
                    continue
                col = 0
                for ch in letters.group(1):
                    col = col * 26 + ord(ch) - ord("A") + 1
                col -= 1
                t = c.attrib.get("t")
                v = c.find(f"{{{_NS_MAIN}}}v")
                inline = c.find(f"{{{_NS_MAIN}}}is")
                value = ""
                if t == "s" and v is not None and v.text is not None:
                    value = shared[int(v.text)]
                elif t == "inlineStr" and inline is not None:
                    value = "".join(x.text or "" for x in inline.iter(f"{{{_NS_MAIN}}}t"))
                elif v is not None and v.text is not None:
                    value = v.text
                values[col] = value
            if values:
                last = max(values)
                rows.append([values.get(i, "") for i in range(last + 1)])
        return sheet_name, rows


def _jd_id(seq: int, company_type: str, sub_category: str) -> str:
    prefix = "IVD" if "IVD" in company_type else "ICL" if "ICL" in company_type else "GEN"
    cat = re.sub(r"\s+", "", sub_category) or "GEN"
    return f"JD-{prefix}-{cat}-{seq:03d}"


def build_jd() -> dict:
    JD_OUT_DIR.mkdir(parents=True, exist_ok=True)
    sheet_name, rows = _xlsx_rows(JD_SOURCE)
    if not rows:
        raise ValueError("JD workbook is empty")
    headers = [str(x).strip() for x in rows[0][:6]]
    expected = ["序号", "公司类型", "子分类", "岗位名称", "核心职责", "任职要求"]
    if headers != expected:
        raise ValueError(f"Unexpected JD headers: {headers}")
    entries = []
    for idx, row in enumerate(rows[1:], start=2):
        vals = (row + [""] * 6)[:6]
        seq_raw, company_type, sub_category, title, duties, requirements = vals
        if not str(title).strip():
            continue
        seq = int(float(seq_raw)) if str(seq_raw).strip() else len(entries) + 1
        entries.append({
            "jd_id": _jd_id(seq, str(company_type), str(sub_category)),
            "seq": seq,
            "company_type": str(company_type).strip(),
            "sub_category": str(sub_category).strip(),
            "title": str(title).strip(),
            "core_responsibilities": str(duties).strip(),
            "requirements_text": str(requirements).strip(),
            "source_file": JD_SOURCE.name,
            "source_sha256": sha256(JD_SOURCE),
            "source_sheet": sheet_name,
            "source_row": idx,
            "source_status": "VERIFIED_SOURCE",
        })
    library = {
        "schema_version": "3.4",
        "source": JD_SOURCE.name,
        "source_sha256": sha256(JD_SOURCE),
        "total": len(entries),
        "entries": entries,
    }
    (JD_OUT_DIR / "jd_library.json").write_text(
        json.dumps(library, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    generated_manifest = {
        "schema_version": "1.0",
        "source_file": JD_SOURCE.name,
        "source_sha256": sha256(JD_SOURCE),
        "source_sheet": sheet_name,
        "total_entries": len(entries),
        "jd_ids": [e["jd_id"] for e in entries],
    }
    (JD_OUT_DIR / "jd_library_manifest.json").write_text(
        json.dumps(generated_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    source_manifest = {
        "schema_version": "1.0",
        "source_file": JD_SOURCE.name,
        "sha256": sha256(JD_SOURCE),
        "sheet": sheet_name,
        "rows": len(entries),
        "status": "VERIFIED_SOURCE",
    }
    (MANIFEST_DIR / "jd_source.json").write_text(
        json.dumps(source_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return library


def build_resume_manifest() -> dict:
    all_files = sorted(p for p in RESUME_SOURCE_DIR.rglob("*") if p.is_file())
    entries = []
    candidate_count = 0
    for p in all_files:
        rel = p.relative_to(RESUME_SOURCE_DIR).as_posix()
        role = "candidate_source"
        if p.suffix.lower() == ".html":
            role = "legacy_report"
        elif rel.startswith("面试纪要/"):
            role = "interview_note"
        elif rel.startswith("岗位JD/"):
            role = "legacy_jd_note"
        elif p.suffix.lower() in {".pdf", ".doc", ".docx"}:
            candidate_count += 1
        else:
            role = "other"
        entries.append({
            "path": rel,
            "sha256": sha256(p),
            "bytes": p.stat().st_size,
            "role": role,
        })
    manifest = {
        "schema_version": "1.0",
        "production_authority": "MYSQL",
        "source_pack_role": "PRIVATE_SOURCE_ARCHIVE_AND_REIMPORT_INPUT",
        "all_source_files": len(all_files),
        "candidate_source_files": candidate_count,
        "complete_backup_of_production_mysql": False,
        "note": "This source pack is not a complete backup of the production MySQL resume database. Production count is dynamic and must be obtained from ResumeRepository/MySQL.",
        "sources": entries,
    }
    (MANIFEST_DIR / "resume_sources.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    profiles = build_profiles()
    jds = build_jd()
    resumes = build_resume_manifest()
    foundation = {
        "schema_version": "3.4",
        "profile_library": {"authority": "SOURCE_PDF", "count": profiles["meta"]["total"]},
        "jd_library": {"authority": "SOURCE_XLSX", "count": jds["total"]},
        "resume_library": {
            "authority": "MYSQL",
            "source_pack_candidate_files": resumes["candidate_source_files"],
            "source_pack_is_complete_mysql_backup": False,
        },
    }
    (ROOT / "data/FOUNDATION_STATE.json").write_text(
        json.dumps(foundation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(foundation, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
