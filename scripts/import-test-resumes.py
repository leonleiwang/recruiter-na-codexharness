#!/usr/bin/env python3
"""把真实简历复制到本地测试样本目录，并生成 manifest.json（不提交 git，全部落在 workspace/ 下）。

真实简历含个人信息，只允许存放在被 .gitignore 忽略的 workspace/ 目录里；
本脚本不写数据库、不改仓库、不调用外部服务。

用法：
  python scripts/import-test-resumes.py --src "D:\\简历目录" --out workspace/test_resumes/inspection_technician \\
      王习之_检验技术员.pdf 周英_检验技术员.pdf ...

manifest.json 记录：文件名、sha256、字节数、页数、PDF 文本层长度、建议解析引擎、导入时间。
同名但内容不同会报错；内容重复（sha256 相同）会标记 duplicate_of 而不是覆盖。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


#: 文本层 CJK 字符数低于该值视为「扫描件/水印层」，需要走 OCR。
MIN_USABLE_CJK = 100


def pdf_info(path: Path) -> tuple[int | None, int | None, int | None, str, bool]:
    """返回 (页数, 文本层字符数, CJK 字符数, 解析引擎建议, 是否需 OCR)。"""
    try:
        import fitz  # PyMuPDF
    except ImportError:  # pragma: no cover - 本机有 pymupdf
        return None, None, None, "pymupdf?", True
    try:
        with fitz.open(str(path)) as doc:
            pages = doc.page_count
            text = "".join(page.get_text() for page in doc)
    except Exception as exc:  # 损坏/加密 PDF
        return None, None, None, f"pdf-open-failed:{type(exc).__name__}", True
    text_len = len(text.strip())
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    needs_ocr = cjk < MIN_USABLE_CJK
    return pages, text_len, cjk, ("windows-ocr" if needs_ocr else "pymupdf"), needs_ocr


def docx_info(path: Path) -> tuple[None, int | None, None, str, bool]:
    try:
        import docx
    except ImportError:  # pragma: no cover
        return None, None, None, "python-docx?", True
    document = docx.Document(str(path))
    text_len = len("\n".join(p.text for p in document.paragraphs).strip())
    needs_ocr = text_len < MIN_USABLE_CJK
    return None, text_len, None, ("python-docx" if not needs_ocr else "windows-ocr"), needs_ocr


def image_info() -> tuple[None, None, None, str, bool]:
    return None, None, None, "windows-ocr", True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="简历来源目录")
    ap.add_argument("--out", required=True, help="样本落盘目录（放在 workspace/ 下）")
    ap.add_argument("--dedupe", action="store_true", default=True, help="内容重复时标记 duplicate_of（默认开）")
    ap.add_argument("names", nargs="+", help="要导入的文件名")
    args = ap.parse_args()

    src = Path(args.src)
    out = Path(args.out)
    if not src.is_dir():
        print(f"来源目录不存在: {src}", file=sys.stderr)
        return 2
    out_abs = (ROOT / out).resolve() if not out.is_absolute() else out.resolve()
    workspace = (ROOT / "workspace").resolve()
    try:
        out_abs.relative_to(workspace)
    except ValueError:
        print(f"拒绝写入 workspace/ 之外的目录: {out_abs}（真实简历含个人信息）", file=sys.stderr)
        return 2
    out_abs.mkdir(parents=True, exist_ok=True)

    manifest_path = out_abs / "manifest.json"
    manifest = {"schema_version": 1, "files": []}
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.setdefault("files", [])
    existing = {row.get("name"): row for row in manifest["files"]}

    imported, skipped, failed = 0, 0, 0
    for name in args.names:
        source = src / name
        if not source.is_file():
            print(f"缺失: {source}")
            failed += 1
            continue
        digest = sha256_of(source)
        suffix = source.suffix.lower()
        if suffix == ".pdf":
            pages, text_len, cjk_chars, engine, needs_ocr = pdf_info(source)
        elif suffix == ".docx":
            pages, text_len, cjk_chars, engine, needs_ocr = docx_info(source)
        elif suffix in (".jpg", ".jpeg", ".png", ".bmp", ".webp"):
            pages, text_len, cjk_chars, engine, needs_ocr = image_info()
        else:
            pages, text_len, cjk_chars, engine, needs_ocr = None, None, None, "plain-text", False

        duplicate_of = next(
            (row["name"] for row in manifest["files"] if row.get("sha256") == digest and row.get("name") != name),
            None,
        )
        target = out_abs / name
        if target.exists() and existing.get(name, {}).get("sha256") not in (None, digest):
            print(f"同名但内容不同，拒绝覆盖: {name}（先人工处理）")
            failed += 1
            continue
        if duplicate_of:
            print(f"内容重复，跳过复制: {name} == {duplicate_of}")
            skipped += 1
        else:
            shutil.copy2(source, target)
            imported += 1

        row = {
            "name": name,
            "sha256": digest,
            "bytes": source.stat().st_size,
            "pages": pages,
            "text_len": text_len,
            "cjk_chars": cjk_chars,
            "needs_ocr": needs_ocr,
            "engine_hint": engine,
            "source": str(source),
            "path": str(target.relative_to(ROOT)) if not duplicate_of else None,
            "imported_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        if duplicate_of:
            row["duplicate_of"] = duplicate_of
        existing[name] = row
        if name in {r["name"] for r in manifest["files"]}:
            manifest["files"] = [row if r["name"] == name else r for r in manifest["files"]]
        else:
            manifest["files"].append(row)
        extra = f" duplicate_of={duplicate_of}" if duplicate_of else ""
        print(f"{name}: pages={pages} text_len={text_len} cjk={cjk_chars} engine={engine}{extra}")

    manifest["source_dir"] = str(src)
    manifest["count"] = len(manifest["files"])
    manifest["note"] = "真实个人信息：仅本地测试样本，workspace/ 已被 .gitignore 忽略，勿提交、勿上传。"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n导入 {imported} 份，跳过重复 {skipped} 份，失败 {failed} 份；manifest: {manifest_path}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
