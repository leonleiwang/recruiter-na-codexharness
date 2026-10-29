#!/usr/bin/env python3
"""Recruiter-Na host-side tool entry point.

v5 product traffic uses only ``workflow handle``. Repository subcommands remain
available for trusted host diagnostics, not for the hiring-manager DingTalk
router. There is no legacy requisition/screen.run command path.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "runtime"))

from rn.hiring_requests import HiringRequestRepository
from rn.hiring_workflow_v5 import HiringManagerWorkflow
from rn.repositories import ProfileRepository, ResumeRepository
from rn.resume_delivery import SelectedResumeDeliveryService
from rn.resume_screening.decision.screening_v5 import HiringDecisionScreeningService
from rn.resume_screening.jd.intake import HiringIntentAnalyzer
from rn.resume_screening.jd.templates import JDRepository


def _json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False))


def _profile_payload(record) -> dict[str, Any]:
    raw = record.raw
    return {
        "entity_type": "profile",
        "profile_id": record.profile_id,
        "name": record.name,
        "source_status": raw.get("source_status"),
        "source_file": raw.get("source_file"),
        "summary": raw.get("description") or raw.get("business_challenge") or "",
        "key_abilities": [x.get("name") for x in raw.get("key_abilities") or [] if isinstance(x, dict)],
        "key_experiences": [x.get("name") for x in raw.get("key_experiences") or [] if isinstance(x, dict)],
        "key_traits": list(raw.get("key_traits") or []),
    }


def _jd_payload(record, match_type: str) -> dict[str, Any]:
    return {
        "entity_type": "jd_template",
        "jd_id": record.jd_id,
        "title": record.title,
        "match_type": match_type,
        "company_type": record.company_type,
        "sub_category": record.sub_category,
        "core_responsibilities": record.core_responsibilities,
        "requirements_text": record.requirements_text,
        "source_file": record.source_file,
        "source_sheet": record.source_sheet,
        "source_row": record.source_row,
    }


def _workflow_handle(argv: list[str]) -> dict[str, Any]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--conversation-id", default="")
    args, _ = parser.parse_known_args(argv)
    profile_repo = ProfileRepository()
    jd_repo = JDRepository()
    resume_repo = ResumeRepository()
    request_repo = HiringRequestRepository()
    delivery = SelectedResumeDeliveryService(resume_repo)
    from rn.resume_downloads import from_env as downloads_from_env
    workflow = HiringManagerWorkflow(
        request_repo=request_repo,
        profile_repo=profile_repo,
        jd_repo=jd_repo,
        resume_repo=resume_repo,
        analyzer=HiringIntentAnalyzer.from_env(),
        delivery_service=delivery,
        download_service=downloads_from_env(request_repo, delivery),
        screening_service=HiringDecisionScreeningService(
            resume_repo=resume_repo,
            profile_repo=profile_repo,
            jd_repo=jd_repo,
        ),
    )
    return workflow.handle(args.owner, args.text, args.conversation_id)


def main() -> int:
    if len(sys.argv) < 2:
        _json({"error": "usage: host_tool.py <workflow|health|profile|jd|resume> ..."})
        return 1
    resource = sys.argv[1]
    action = sys.argv[2] if len(sys.argv) > 2 else ""
    try:
        if resource == "workflow":
            result = _workflow_handle(sys.argv[3:]) if action == "handle" else {"error": f"unknown workflow action: {action}"}
        elif resource == "health":
            pr, jr, rr = ProfileRepository(), JDRepository(), ResumeRepository()
            ph, jh, rh = pr.health(), jr.health(), rr.health()
            result = {
                "profile": {"status": ph.status, "backend": ph.backend, "count": ph.count},
                "jd": {"status": jh.status, "backend": jh.backend, "count": jh.count},
                "resume": {"status": rh.status, "backend": rh.backend, "count": rh.count, "error": rh.error},
            }
        elif resource == "profile":
            repo = ProfileRepository()
            key = sys.argv[3] if len(sys.argv) > 3 else ""
            if action == "count": result = {"count": repo.count(), "authority": "VERIFIED_SOURCE_PDF"}
            elif action == "search": result = {"query": key, "items": [_profile_payload(x) for x in repo.search(key)]}
            elif action == "get":
                item = repo.get(key); result = _profile_payload(item) if item else {"error": "not_found"}
            else: result = {"error": f"unknown action: {action}"}
        elif resource == "jd":
            repo = JDRepository()
            key = sys.argv[3] if len(sys.argv) > 3 else ""
            if action == "count": result = {"count": repo.count(), "authority": "VERIFIED_SOURCE_XLSX"}
            elif action == "search_exact": result = {"query": key, "items": [_jd_payload(x, "exact") for x in repo.search_exact(key)]}
            elif action == "related": result = {"query": key, "items": [_jd_payload(x, "related") for x in repo.search_related(key)]}
            elif action == "get":
                item = repo.get(key); result = _jd_payload(item, "id") if item else {"error": "not_found"}
            else: result = {"error": f"unknown action: {action}"}
        elif resource == "resume":
            repo = ResumeRepository()
            key = sys.argv[3] if len(sys.argv) > 3 else ""
            if action == "count":
                health = repo.health(); result = {"count": repo.count(), "backend": health.backend, "authority": "MYSQL"}
            elif action == "get":
                item = repo.get_candidate(key); result = item if item else {"error": "not_found"}
            else:
                result = {"error": "resume full-library browsing is not exposed by this CLI; use workflow screening"}
        else:
            result = {"error": f"unknown resource: {resource}"}
        _json(result)
        return 1 if isinstance(result, dict) and result.get("error") else 0
    except Exception as exc:
        _json({"error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
