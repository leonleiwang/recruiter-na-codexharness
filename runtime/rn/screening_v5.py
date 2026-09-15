"""Recruiter-Na v5 Candidate Decision screening service.

This is the active candidate-screening path for v5. It deliberately contains
no formal candidate score calculation. LLM output is semantic evidence only;
final pass/fail and recommendation ordering are deterministic Python.
"""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from rn.candidate_decision_v5 import decide_candidate, recommendation_order_key
from rn.criteria_adapter_v5 import build_actual_criteria, load_inspection_technician_policy, supports_inspection_technician
from rn.decision_evidence_evaluator import DecisionEvidenceEvaluator, validate_decision_evidence_contract
from rn.evidence_grounding import repair_decision_grounding, validate_decision_grounding
from rn.job_contract_v3 import normalize_resume_facts
from rn.progress import emit_progress

ROOT = Path(__file__).resolve().parents[2]


class UnsupportedScreeningPolicy(RuntimeError):
    def __init__(self, code: str = "SCREENING_POLICY_UNSUPPORTED_ROLE"):
        super().__init__(code)
        self.code = code


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _profile_for_request(request: dict[str, Any], profile_repo: Any) -> Any | None:
    match = (request.get("request") or {}).get("profile_match") or {}
    if match.get("status") != "MATCHED":
        return None
    pid = str(match.get("profile_id") or "")
    return profile_repo.get(pid) if pid and hasattr(profile_repo, "get") else None


def _recommendation_reasons(criteria: dict[str, Any], assessment: dict[str, Any], decision: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for signal in decision.get("preference_signals") or []:
        text = str(signal.get("evidence") or signal.get("reason") or "").strip()
        label = str(signal.get("item") or "加分项")
        rendered = f"{label}：{text}" if text else label
        if rendered not in out:
            out.append(rendered)
    defs = {str(x.get("criterion_id") or ""): x for x in (criteria.get("criteria") or [])}
    for row in assessment.get("criteria_assessments") or []:
        cid = str(row.get("criterion_id") or "")
        definition = defs.get(cid) or {}
        if definition.get("decision_mode") != "resume_evidence":
            continue
        if row.get("criterion_result") != "satisfied" or row.get("evidence_status") != "confirmed":
            continue
        evidence = row.get("evidence") or []
        quote = str((evidence[0] if evidence else {}).get("text") or "").strip()
        label = str(definition.get("name") or cid)
        rendered = f"{label}：{quote}" if quote else label
        if rendered not in out:
            out.append(rendered)
    return out[:4]


def evaluate_candidate_v5(
    resume: dict[str, Any], *, request: dict[str, Any], profile: Any | None,
    evaluator: Any | None = None, criteria: dict[str, Any] | None = None,
    trace_dir: str | Path | None = None,
) -> dict[str, Any]:
    evaluator = evaluator or DecisionEvidenceEvaluator.from_env()
    actual = criteria or build_actual_criteria(request, profile)
    facts = normalize_resume_facts(resume)
    trace = Path(trace_dir) if trace_dir is not None else None
    if trace:
        trace.mkdir(parents=True, exist_ok=True)
        _write_json(trace / "resume_facts.json", facts)

    started = time.perf_counter()
    timing: dict[str, Any] = {
        "schema_version": "5.0.2",
        "candidate_id": facts.get("candidate_id"),
        "status": "running",
        "llm_evaluation_ms": 0,
        "contract_validation_ms": 0,
        "grounding_repair_ms": 0,
        "grounding_validation_ms": 0,
        "decision_ms": 0,
        "total_ms": 0,
    }
    try:
        t0 = time.perf_counter()
        evidence = evaluator.evaluate(actual, facts)
        timing["llm_evaluation_ms"] = round((time.perf_counter() - t0) * 1000, 3)

        t0 = time.perf_counter()
        evidence = validate_decision_evidence_contract(actual, facts, evidence)
        timing["contract_validation_ms"] = round((time.perf_counter() - t0) * 1000, 3)
        if trace:
            _write_json(trace / "decision_evidence_raw.json", evidence)

        t0 = time.perf_counter()
        evidence, repair_report = repair_decision_grounding(facts, evidence)
        # Repair may downgrade definitive outcomes; validate the repaired contract
        # before deterministic decision logic consumes it.
        evidence = validate_decision_evidence_contract(actual, facts, evidence)
        timing["grounding_repair_ms"] = round((time.perf_counter() - t0) * 1000, 3)

        t0 = time.perf_counter()
        grounding = validate_decision_grounding(facts, evidence)
        timing["grounding_validation_ms"] = round((time.perf_counter() - t0) * 1000, 3)
        if grounding.get("status") != "GROUNDED":
            raise RuntimeError("DECISION_EVIDENCE_GROUNDING_FAILED:" + ",".join(grounding.get("errors") or []))

        t0 = time.perf_counter()
        decision = decide_candidate(actual, evidence, facts)
        decision["candidate_summary"] = {
            "degree": facts.get("degree"),
            "current_company": facts.get("current_company"),
            "current_position": facts.get("current_position"),
            "total_work_years": facts.get("total_work_years"),
            "location": facts.get("location"),
        }
        decision["recommendation_reasons"] = _recommendation_reasons(actual, evidence, decision)
        timing["decision_ms"] = round((time.perf_counter() - t0) * 1000, 3)

        if trace:
            _write_json(trace / "decision_evidence.json", evidence)
            _write_json(trace / "grounding_repair.json", repair_report)
            _write_json(trace / "decision_evidence_validation.json", grounding)
            _write_json(trace / "candidate_decision.json", decision)
            _write_json(trace / "state.json", {
                "schema_version": "5.0.2",
                "candidate_id": facts.get("candidate_id"),
                "state": "DECISION_COMPLETE",
                "transitions": ["RESUME_FACTS_READY", "DECISION_EVIDENCE_READY", "GROUNDING_REPAIRED", "EVIDENCE_GROUNDED", "DECISION_COMPLETE"],
            })
        timing["status"] = "complete"
        return decision
    except Exception as exc:
        timing["status"] = "failed"
        timing["error"] = str(exc)
        raise
    finally:
        timing["total_ms"] = round((time.perf_counter() - started) * 1000, 3)
        if trace:
            _write_json(trace / "timing.json", timing)


class HiringDecisionScreeningService:
    def __init__(self, *, resume_repo: Any, profile_repo: Any, jd_repo: Any, evaluator: Any | None = None,
                 run_root: str | Path | None = None, pool_limit: int | None = None):
        self.resumes = resume_repo
        self.profiles = profile_repo
        self.jds = jd_repo
        self.evaluator = evaluator or DecisionEvidenceEvaluator.from_env()
        self.run_root = Path(run_root) if run_root is not None else ROOT / "workspace" / "runs"
        self.pool_limit = int(pool_limit or os.environ.get("RECRUITER_SCREEN_POOL_LIMIT", "30"))

    def _pool(self, request: dict[str, Any], profile: Any | None) -> list[dict[str, Any]]:
        fields = (request.get("request") or {}).get("fields") or {}
        queries = [str(fields.get("position_title") or request.get("position_title") or "").strip()]
        if profile is not None:
            queries.append(str(getattr(profile, "name", "") or ""))
        policy = load_inspection_technician_policy()
        queries.extend(str(x).strip() for x in (policy.get("retrieval_queries") or []) if str(x).strip())
        collected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for query in [q for q in queries if q]:
            try:
                rows = self.resumes.search_candidates(query, limit=self.pool_limit)
            except Exception:
                rows = []
            for row in rows:
                cid = str(row.get("candidate_id") or row.get("resume_id") or row.get("name") or "")
                if not cid or cid in seen:
                    continue
                seen.add(cid)
                collected.append(row)
                if len(collected) >= self.pool_limit:
                    return collected
        if not collected:
            for row in self.resumes.search_candidates("", limit=self.pool_limit):
                cid = str(row.get("candidate_id") or row.get("resume_id") or row.get("name") or "")
                if cid and cid not in seen:
                    seen.add(cid)
                    collected.append(row)
                    if len(collected) >= self.pool_limit:
                        break
        return collected

    def screen_request(self, request: dict[str, Any]) -> dict[str, Any]:
        screening_started = time.perf_counter()
        request_id = str(request.get("request_id") or "")
        profile = _profile_for_request(request, self.profiles)
        if not supports_inspection_technician(request, profile):
            raise UnsupportedScreeningPolicy()
        actual = build_actual_criteria(request, profile)
        resume_count = int(self.resumes.count())
        pool = self._pool(request, profile)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        run_dir = self.run_root / f"{stamp}-{request_id}-decision"
        run_dir.mkdir(parents=True, exist_ok=True)
        _write_json(run_dir / "data_sources.json", {
            "schema_version": "5.0.2",
            "request_id": request_id,
            "policy": {"policy_id": actual.get("policy_id"), "authority": "RECRUITER_V2.3_ACCEPTED"},
            "profile": {"matched": bool(profile), "profile_id": getattr(profile, "profile_id", None)},
            "resume": {"count": resume_count, "candidate_pool_count": len(pool)},
        })
        _write_json(run_dir / "request_snapshot.json", request)
        _write_json(run_dir / "decision_policy.json", load_inspection_technician_policy())
        _write_json(run_dir / "actual_criteria.json", actual)
        emit_progress("RESUME_FETCH", f"已建立 {len(pool)} 人候选池，开始逐份执行候选人决策…", current=0, total=len(pool))

        decisions: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        workers = max(1, int(os.environ.get("RECRUITER_DECISION_WORKERS", "3")))

        def one(index_resume: tuple[int, dict[str, Any]]) -> tuple[int, dict[str, Any] | None, dict[str, Any] | None, float]:
            idx, resume = index_resume
            cid = str(resume.get("candidate_id") or resume.get("resume_id") or f"candidate-{idx}")
            candidate_started = time.perf_counter()
            try:
                result = evaluate_candidate_v5(
                    resume,
                    request=request,
                    profile=profile,
                    evaluator=self.evaluator,
                    criteria=actual,
                    trace_dir=run_dir / "candidates" / cid,
                )
                result["_pool_index"] = idx
                elapsed_ms = round((time.perf_counter() - candidate_started) * 1000, 3)
                return idx, result, None, elapsed_ms
            except Exception as exc:
                elapsed_ms = round((time.perf_counter() - candidate_started) * 1000, 3)
                failure = {
                    "candidate_id": cid,
                    "name": resume.get("name"),
                    "evaluation_status": "FAILED",
                    "error": str(exc),
                    "elapsed_ms": elapsed_ms,
                }
                _write_json(run_dir / "candidates" / cid / "technical_failure.json", failure)
                return idx, None, failure, elapsed_ms

        completed = 0
        candidate_elapsed_ms: list[float] = []
        if pool:
            with ThreadPoolExecutor(max_workers=min(workers, len(pool))) as executor:
                futures = [executor.submit(one, item) for item in enumerate(pool, 1)]
                for future in as_completed(futures):
                    _, decision, failure, elapsed_ms = future.result()
                    candidate_elapsed_ms.append(elapsed_ms)
                    if decision is not None:
                        decisions.append(decision)
                    if failure is not None:
                        failures.append(failure)
                    completed += 1
                    emit_progress(
                        "CANDIDATE_DECISION",
                        f"正在评估候选人 {completed}/{len(pool)}…（本个 {elapsed_ms / 1000:.1f}s）",
                        current=completed, total=len(pool),
                    )

        passed = [x for x in decisions if x.get("decision") == "pass"]
        rejected = [x for x in decisions if x.get("decision") == "fail"]
        passed.sort(key=lambda x: recommendation_order_key(x, int(x.get("_pool_index") or 0)))
        rejected.sort(key=lambda x: int(x.get("_pool_index") or 0))
        failures.sort(key=lambda x: str(x.get("candidate_id") or ""))

        recommendations: list[dict[str, Any]] = []
        for order, item in enumerate(passed, 1):
            recommendations.append({
                "recommendation_order": order,
                "candidate_id": item.get("candidate_id"),
                "name": item.get("name"),
                "decision": "pass",
                "readiness": "needs_verification" if item.get("needs_verification") else "ready",
                "needs_verification": item.get("needs_verification") or [],
                "interview_focus": item.get("interview_focus") or [],
                "preference_signals": item.get("preference_signals") or [],
                "recommendation_reasons": item.get("recommendation_reasons") or [],
                "summary": item.get("candidate_summary") or {},
            })

        rejected_candidates = []
        for item in rejected:
            rejected_candidates.append({
                "candidate_id": item.get("candidate_id"),
                "name": item.get("name"),
                "decision": "fail",
                "fail_reasons": item.get("fail_reasons") or [],
                "veto_items": item.get("veto_items") or [],
            })

        screening_elapsed_ms = round((time.perf_counter() - screening_started) * 1000, 3)
        performance = {
            "worker_count": min(workers, len(pool)) if pool else 0,
            "screening_elapsed_ms": screening_elapsed_ms,
            "candidate_elapsed_ms": candidate_elapsed_ms,
            "candidate_max_elapsed_ms": max(candidate_elapsed_ms) if candidate_elapsed_ms else 0,
        }
        snapshot = {
            "schema_version": "5.0.2",
            "request_id": request_id,
            "resume_count": resume_count,
            "candidate_pool_count": len(pool),
            "evaluated_count": len(decisions),
            "pass_count": len(recommendations),
            "reject_count": len(rejected_candidates),
            "technical_failure_count": len(failures),
            "recommendations": recommendations,
            "rejected_candidates": rejected_candidates,
            "failures": failures,
            "performance": performance,
        }
        _write_json(run_dir / "decision_snapshot.json", snapshot)
        emit_progress(
            "DONE",
            f"候选人决策完成：{len(recommendations)} 人可推荐，{len(rejected_candidates)} 人不通过，{len(failures)} 人技术评估失败。",
            current=len(pool), total=len(pool), status="done",
        )
        return {**snapshot, "trace_dir": str(run_dir)}
