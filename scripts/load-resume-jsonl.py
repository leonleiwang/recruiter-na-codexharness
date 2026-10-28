#!/usr/bin/env python3
"""把导出的 resumes.jsonl 导入本地 JSON 简历库（开发/评估用，不依赖 MySQL）。

用法：
  python scripts/load-resume-jsonl.py --jsonl <resumes.jsonl> [--out data/dev/resume_db.fixture.json]
导入后配合 RN_BACKEND=json + RN_ALLOW_JSON_DEV=true 即可跑筛选链。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", required=True)
    ap.add_argument("--out", default="data/dev/resume_db.fixture.json")
    args = ap.parse_args()

    rows = []
    skipped = 0
    for line in Path(args.jsonl).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        raw_text = str(row.get("raw_text") or "").strip()
        if len(raw_text) < 120:
            skipped += 1
            continue
        rid = str(row.get("id") or row.get("resume_id") or row.get("name") or "")
        rows.append({
            "resume_id": rid,
            "candidate_id": rid,
            "name": str(row.get("name") or "").strip(),
            "degree": str(row.get("degree") or "").strip(),
            "major": str(row.get("major") or "").strip(),
            "resume_name": str(row.get("resume_name") or "").strip(),
            "raw_text": raw_text,
            "source": "parsed-export",
            "status": "REVIEW_REQUIRED",
        })

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"version": 1, "resumes": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("导入 %d 条 | 跳过（正文过短）%d 条 -> %s" % (len(rows), skipped, out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
