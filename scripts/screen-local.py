#!/usr/bin/env python3
"""在本地 JSON 简历库上跑生产同一条筛选链（Candidate Decision + 推荐顺序）。

用法：
  python scripts/screen-local.py --title 检验技术员 --profile-id PROFILE-412167F2937C [--location 杭州]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file(ROOT / ".env.local")

from rn.repositories import JDRepository, ProfileRepository, ResumeRepository  # noqa: E402
from rn.screening_v5 import HiringDecisionScreeningService  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", default="检验技术员")
    ap.add_argument("--location", default="杭州")
    ap.add_argument("--profile-id", default="")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--pool-limit", type=int, default=0, help="候选池上限（0=用环境默认）")
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    repo = ResumeRepository()
    health = repo.health()
    print("简历库: backend=%s status=%s count=%s" % (health.backend, health.status, health.count))
    if not health.count:
        print("库是空的：先用 scripts/load-resume-jsonl.py 导入，或配置 MySQL。")
        return 2

    request = {
        "request_id": "REQ-LOCAL",
        "status": "JD_READY",
        "position_title": args.title,
        "request": {
            "fields": {"position_title": args.title, "location": args.location},
            "profile_match": {"status": "MATCHED", "profile_id": args.profile_id},
        },
    }
    svc = HiringDecisionScreeningService(
        resume_repo=repo, profile_repo=ProfileRepository(), jd_repo=JDRepository(),
        **(  {"pool_limit": args.pool_limit} if args.pool_limit else {} ),
    )
    result = svc.screen_request(request)
    recs = result.get("recommendations") or []
    print("候选池/评估: 简历总数=%s 推荐=%s 否决=%s 技术失败=%s" % (
        result.get("resume_count"), result.get("pass_count"),
        result.get("reject_count"), result.get("technical_failure_count")))
    print()
    print("=== 推荐顺序（前 %d）===" % args.top)
    for item in recs[: args.top]:
        print("  %2d. %-8s | %s" % (item.get("recommendation_order"), item.get("name"), item.get("decision")))
        for reason in (item.get("recommendation_reasons") or [])[:2]:
            print("        依据:", str(reason)[:80])
        for v in (item.get("needs_verification") or [])[:2]:
            print("        待核实:", str(v.get("item"))[:60])
    if args.json:
        target = Path(args.json)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print("\n完整结果:", target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
