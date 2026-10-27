#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
from rn.hiring_requests import HiringRequestRepository
from rn.repositories import ProfileRepository, ResumeRepository
from rn.resume_screening.jd.templates import JDRepository
from rn.resume_screening.scoring.screening_v4 import HiringScreeningService

p = argparse.ArgumentParser(description='Run a 3-candidate v4 trace without changing recommendation cursor')
p.add_argument('--owner', required=True)
p.add_argument('--request-id', required=True)
p.add_argument('--pool-limit', type=int, default=3)
args = p.parse_args()
repo = HiringRequestRepository()
request = repo.get_for_owner(args.owner, args.request_id)
if request is None:
    raise SystemExit('REQUEST_NOT_FOUND_OR_NOT_OWNED')
service = HiringScreeningService(
    resume_repo=ResumeRepository(), profile_repo=ProfileRepository(), jd_repo=JDRepository(),
    pool_limit=args.pool_limit,
)
result = service.rank_request(request)
print(json.dumps(result, ensure_ascii=False, indent=2))
