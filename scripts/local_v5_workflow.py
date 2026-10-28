#!/usr/bin/env python3
"""Local driver for the v5 hiring workflow (same entry as the DingTalk host tool).

Usage:
  python scripts/local_v5_workflow.py --owner <owner> --text "<message>" [--conv <id>]

Loads .env.local first so MySQL / LLM settings match the host deployment.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--conv", default="conv-local-dsh")
    args = parser.parse_args()

    load_env(ROOT / ".env.local")
    sys.path.insert(0, str(ROOT / "runtime"))

    from rn.hiring_requests import HiringRequestRepository
    from rn.hiring_workflow_v5 import HiringManagerWorkflow
    from rn.repositories import ProfileRepository, ResumeRepository
    from rn.resume_delivery import SelectedResumeDeliveryService
    from rn.resume_screening.decision.screening_v5 import HiringDecisionScreeningService
    from rn.resume_screening.jd.intake import HiringIntentAnalyzer
    from rn.resume_screening.jd.templates import JDRepository

    profile_repo = ProfileRepository()
    jd_repo = JDRepository()
    resume_repo = ResumeRepository()
    workflow = HiringManagerWorkflow(
        request_repo=HiringRequestRepository(),
        profile_repo=profile_repo,
        jd_repo=jd_repo,
        resume_repo=resume_repo,
        analyzer=HiringIntentAnalyzer.from_env(),
        delivery_service=SelectedResumeDeliveryService(resume_repo),
        screening_service=HiringDecisionScreeningService(
            resume_repo=resume_repo,
            profile_repo=profile_repo,
            jd_repo=jd_repo,
        ),
    )
    result = workflow.handle(args.owner, args.text, args.conv)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
