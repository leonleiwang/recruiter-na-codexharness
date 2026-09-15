#!/usr/bin/env python3
"""Data Doctor CLI — check the health of all three data sources.

Usage:
  python -m rn.data_doctor
  python -m rn.data_doctor --json
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "runtime"))

from rn.repositories import ProfileRepository, JDRepository, ResumeRepository, DataSourcesSnapshot, DataSourceHealth


def check_all() -> DataSourcesSnapshot:
    snapshot = DataSourcesSnapshot()
    try:
        pr = ProfileRepository()
        snapshot.profile_library = pr.health()
    except Exception as exc:
        snapshot.profile_library = DataSourceHealth(status="unavailable", error=str(exc))

    try:
        jr = JDRepository()
        snapshot.jd_library = jr.health()
    except Exception as exc:
        snapshot.jd_library = DataSourceHealth(status="unavailable", error=str(exc))

    try:
        rr = ResumeRepository()
        snapshot.resume_library = rr.health()
    except Exception as exc:
        snapshot.resume_library = DataSourceHealth(status="unavailable", error=str(exc))

    return snapshot


def main() -> int:
    json_mode = "--json" in sys.argv
    snapshot = check_all()

    if json_mode:
        print(json.dumps({
            "profile_library": {"status": snapshot.profile_library.status, "backend": snapshot.profile_library.backend,
                                "count": snapshot.profile_library.count, "error": snapshot.profile_library.error},
            "jd_library": {"status": snapshot.jd_library.status, "backend": snapshot.jd_library.backend,
                           "count": snapshot.jd_library.count, "error": snapshot.jd_library.error},
            "resume_library": {"status": snapshot.resume_library.status, "backend": snapshot.resume_library.backend,
                               "count": snapshot.resume_library.count, "error": snapshot.resume_library.error},
        }, ensure_ascii=False, indent=2))
    else:
        print("Data Doctor — Recruiter-Na v5.0.2")
        print(f"  Profile Library: {snapshot.profile_library.status.upper()} ({snapshot.profile_library.count} profiles)")
        if snapshot.profile_library.error:
            print(f"    Error: {snapshot.profile_library.error}")
        print(f"  JD Library:      {snapshot.jd_library.status.upper()} ({snapshot.jd_library.count} templates)")
        if snapshot.jd_library.error:
            print(f"    Error: {snapshot.jd_library.error}")
        print(f"  Resume Database:  {snapshot.resume_library.status.upper()} ({snapshot.resume_library.backend}, {snapshot.resume_library.count} resumes)")
        if snapshot.resume_library.error:
            print(f"    Error: {snapshot.resume_library.error}")

    all_ok = (snapshot.profile_library.status == "ok" and
              snapshot.jd_library.status == "ok" and
              snapshot.resume_library.status == "ok")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())