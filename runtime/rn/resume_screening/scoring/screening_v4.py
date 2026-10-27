"""Recruiter-Na v4 candidate ranking.

The v4 scorer separates standard-profile evidence from request-JD evidence.
LLMs only emit evidence; deterministic code computes ProfileMatch, JDMatch,
JM=40/60 (or JD-only), RQ, and final CS.
"""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from rn.resume_screening.decision.evidence_grounding import validate_grounding
from rn.resume_screening.decision.job_contract_v3 import build_job_contract, normalize_resume_facts
from rn.progress import emit_progress
from rn.resume_screening.scoring.scoring_engine_v3 import score_candidate
from rn.resume_screening.scoring.scoring_v4 import combine_component_scores
from rn.resume_screening.decision.semantic_evidence_evaluator import EvidenceEvaluationError, SemanticEvidenceEvaluator

ROOT = Path(__file__).resolve().parents[4]


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _normalize_weights(tags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not tags:
        raise ValueError("JD_SCORING_TAGS_MISSING")
    total = sum(max(0.0, float(x.get("weight") or 0)) for x in tags)
    if total <= 0:
        raise ValueError("JD_SCORING_TAG_WEIGHTS_INVALID")
    out = []
    running = 0.0
    for idx, item in enumerate(tags):
        row = dict(item)
        if idx == len(tags) - 1:
            weight = round(1.0 - running, 6)
        else:
            weight = round(max(0.0, float(item.get("weight") or 0)) / total, 6)
            running += weight
        row["weight"] = weight
        out.append(row)
    return out


def build_profile_contract_v4(request_id: str, profile: dict[str, Any]) -> dict[str, Any]:
    return build_job_contract({
        "job_id": f"{request_id}::PROFILE",
        "title": profile.get("name") or "Standard Profile",
        "hard_requirements": [],
    }, profile=profile)


def build_jd_contract_v4(request_id: str, jd: dict[str, Any]) -> dict[str, Any]:
    groups: list[tuple[str, list[str], float]] = [
        ("responsibility", [str(x).strip() for x in jd.get("responsibilities") or [] if str(x).strip()], 0.45),
        ("experience_skill", [str(x).strip() for x in jd.get("key_experience_skills") or [] if str(x).strip()], 0.45),
        ("bonus", [str(x).strip() for x in jd.get("bonus_points") or [] if str(x).strip()], 0.10),
    ]
    raw_tags: list[dict[str, Any]] = []
    for category, items, group_weight in groups:
        if not items:
            continue
        per = group_weight / len(items)
        for item in items:
            raw_tags.append({"name": item, "category": category, "definition": item, "weight": per})
    tags = _normalize_weights(raw_tags)
    converted = []
    for idx, tag in enumerate(tags, 1):
        converted.append({
            "tag_id": f"J{idx:02d}",
            "name": tag["name"],
            "category": tag["category"],
            "weight": tag["weight"],
            "definition": tag["definition"],
            "positive_evidence_guide": "寻找候选人亲自完成的具体行为、项目或结果；量化结果可判 QUANTIFIED_CASE，完整行为案例可判 CASE。",
            "boundary": "仅出现术语或自我评价而无具体行为时最多 WEAK；没有支持事实则 NO_EVIDENCE。",
        })
    requirements = []
    for idx, item in enumerate(jd.get("hard_requirements") or [], 1):
        if isinstance(item, str):
            name, value, gate = item, True, "HARD_GATE"
        else:
            name = str(item.get("name") or "").strip()
            value = item.get("value")
            gate = str(item.get("gate_type") or "HARD_GATE")
        if not name:
            continue
        rtype = "location" if "地点" in name or "城市" in name else "other"
        policy = (
            "当前城市不同、期望城市不同或简历未写工作意愿时一律 UNKNOWN；只有候选人明确表示不接受目标地点/现场办公等直接冲突事实才可 FAIL。"
            if rtype == "location" else
            "仅以简历中的明确事实判断 PASS/FAIL；信息缺失、语义不足或无法确定时必须 UNKNOWN。"
        )
        requirements.append({
            "requirement_id": f"JR{idx:02d}", "name": name, "value": value,
            "gate_type": gate if gate in {"HARD_GATE", "VERIFY_ONLY"} else "HARD_GATE",
            "requirement_type": rtype, "elasticity": None, "decision_policy": policy,
        })
    return {
        "schema_version": "3.0",
        "job_id": f"{request_id}::JD",
        "job_name": jd.get("title") or "Hiring Request JD",
        "job_context": {
            "department": jd.get("department"), "location": jd.get("location"),
            "responsibilities": jd.get("responsibilities") or [],
            "requirements_text": jd.get("key_experience_skills") or [],
        },
        "tags": converted,
        "requirements": requirements,
    }


def _eval_component(evaluator: Any, contract: dict[str, Any], facts: dict[str, Any], trace: Path | None, prefix: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    try:
        evidence = evaluator.evaluate(contract, facts)
    except EvidenceEvaluationError as exc:
        raise RuntimeError(f"{prefix.upper()}_EVIDENCE_EVALUATION_FAILED:{exc.code}") from exc
    grounding = validate_grounding(facts, evidence)
    if grounding.get("status") != "GROUNDED":
        raise RuntimeError(f"{prefix.upper()}_EVIDENCE_GROUNDING_FAILED:{','.join(grounding.get('errors') or [])}")
    score = score_candidate(facts, contract, evidence)
    if score.get("scoring_status") != "SCORING_COMPLETE":
        raise RuntimeError(f"{prefix.upper()}_SCORING_FAILED:{score.get('scoring_status')}")
    if trace:
        _write_json(trace / f"{prefix}_semantic_evidence.json", evidence)
        _write_json(trace / f"{prefix}_evidence_validation.json", grounding)
        _write_json(trace / f"{prefix}_component_score.json", score)
    return evidence, grounding, score


def _strengths(*component_scores: dict[str, Any] | None) -> list[str]:
    rows = []
    for comp in component_scores:
        if not comp:
            continue
        for tag in ((comp.get("jm") or {}).get("tag_details") or []):
            rows.append((float(tag.get("contribution") or 0), str(tag.get("name") or ""), str(tag.get("reasoning_summary") or "")))
    rows.sort(reverse=True)
    out = []
    for _, name, reason in rows:
        text = f"{name}：{reason}" if reason else name
        if text and text not in out:
            out.append(text)
    return out[:4]


def score_candidate_v4(resume: dict[str, Any], *, request_id: str, jd: dict[str, Any], profile: dict[str, Any] | None,
                       evaluator: Any | None = None, trace_dir: str | Path | None = None, as_of: str | None = None) -> dict[str, Any]:
    evaluator = evaluator or SemanticEvidenceEvaluator.from_env()
    facts = normalize_resume_facts(resume)
    trace = Path(trace_dir) if trace_dir is not None else None
    if trace:
        trace.mkdir(parents=True, exist_ok=True)
        _write_json(trace / "resume_facts.json", facts)

    profile_evidence = None
    profile_grounding = None
    profile_score = None
    if profile:
        profile_contract = build_profile_contract_v4(request_id, profile)
        if trace:
            _write_json(trace / "profile_contract.json", profile_contract)
        profile_evidence, profile_grounding, profile_score = _eval_component(evaluator, profile_contract, facts, trace, "profile")

    jd_contract = build_jd_contract_v4(request_id, jd)
    if trace:
        _write_json(trace / "jd_contract.json", jd_contract)
    jd_evidence, jd_grounding, jd_score = _eval_component(evaluator, jd_contract, facts, trace, "jd")

    rq = (jd_score.get("rq") or {}).get("score")
    pscore = (profile_score.get("jm") or {}).get("score") if profile_score else None
    jscore = (jd_score.get("jm") or {}).get("score")
    blend = combine_component_scores(
        rq_score=float(rq), profile_score=float(pscore) if pscore is not None else None,
        jd_score=float(jscore), profile_matched=bool(profile), veto=bool(jd_score.get("veto")),
    )
    needs = list(dict.fromkeys([*(jd_score.get("needs_verification") or []), *((profile_score or {}).get("needs_verification") or [])]))
    result = {
        "schema_version": "4.0",
        "scoring_status": "SCORING_COMPLETE",
        "candidate_id": facts["candidate_id"],
        "name": facts.get("name"),
        "request_id": request_id,
        "rq": jd_score.get("rq"),
        "profile_match": {"score": pscore, "component": profile_score.get("jm") if profile_score else None} if profile_score else None,
        "jd_match": {"score": jscore, "component": jd_score.get("jm")},
        "jm": {"score": blend["jm"]},
        "cs": blend["cs"],
        "blend": blend,
        "veto": bool(jd_score.get("veto")),
        "veto_reasons": jd_score.get("veto_reasons") or [],
        "needs_verification": needs,
        "strengths": _strengths(profile_score, jd_score),
        "candidate_summary": {
            "degree": facts.get("degree"),
            "current_company": facts.get("current_company"),
            "current_position": facts.get("current_position"),
            "total_work_years": facts.get("total_work_years"),
            "location": facts.get("location"),
        },
        "recommendation_level": jd_score.get("recommendation_level") if blend["cs"] == -1 else (
            "STRONG_RECOMMEND" if blend["cs"] >= 75 else "RECOMMEND" if blend["cs"] >= 55 else "PENDING" if blend["cs"] >= 35 else "WEAK"
        ),
    }
    if trace:
        _write_json(trace / "semantic_evidence.json", {"profile": profile_evidence, "jd": jd_evidence})
        _write_json(trace / "evidence_validation.json", {"profile": profile_grounding, "jd": jd_grounding})
        _write_json(trace / "score_result.json", result)
        _write_json(trace / "state.json", {"candidate_id": facts["candidate_id"], "state": "SCORING_COMPLETE", "transitions": [
            "RESUME_FACTS_READY", "PROFILE_EVIDENCE_READY" if profile else "PROFILE_SKIPPED", "JD_EVIDENCE_READY", "EVIDENCE_VALIDATED", "SCORING_COMPLETE"
        ]})
    return result


class HiringScreeningService:
    def __init__(self, *, resume_repo: Any, profile_repo: Any, jd_repo: Any, evaluator: Any | None = None,
                 run_root: str | Path | None = None, pool_limit: int | None = None):
        self.resumes = resume_repo
        self.profiles = profile_repo
        self.jds = jd_repo
        self.evaluator = evaluator or SemanticEvidenceEvaluator.from_env()
        self.run_root = Path(run_root) if run_root is not None else ROOT / "workspace" / "runs"
        self.pool_limit = int(pool_limit or os.environ.get("RECRUITER_SCREEN_POOL_LIMIT", "30"))

    def _pool(self, request: dict[str, Any], profile: Any | None) -> list[dict[str, Any]]:
        fields = (request.get("request") or {}).get("fields") or {}
        queries = [str(fields.get("position_title") or request.get("position_title") or "").strip()]
        if profile is not None:
            queries.append(str(getattr(profile, "name", "") or ""))
        jd = request.get("jd") or {}
        for value in (jd.get("key_experience_skills") or [])[:4]:
            if len(str(value)) <= 20:
                queries.append(str(value))
        collected = []
        seen = set()
        for q in [x for x in queries if x]:
            try:
                rows = self.resumes.search_candidates(q, limit=self.pool_limit)
            except Exception:
                rows = []
            for row in rows:
                cid = str(row.get("candidate_id") or row.get("resume_id") or row.get("name") or "")
                if not cid or cid in seen:
                    continue
                seen.add(cid); collected.append(row)
                if len(collected) >= self.pool_limit:
                    return collected
        if not collected:
            rows = self.resumes.search_candidates("", limit=self.pool_limit)
            for row in rows:
                cid = str(row.get("candidate_id") or row.get("resume_id") or row.get("name") or "")
                if cid and cid not in seen:
                    seen.add(cid); collected.append(row)
                    if len(collected) >= self.pool_limit:
                        break
        return collected

    def rank_request(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id = str(request["request_id"])
        match = (request.get("request") or {}).get("profile_match") or {}
        profile = self.profiles.get(str(match.get("profile_id") or "")) if match.get("status") == "MATCHED" else None
        jd = request.get("jd") or {}
        if not jd:
            raise RuntimeError("REQUEST_JD_MISSING")
        resume_count = int(self.resumes.count())
        pool = self._pool(request, profile)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        run_dir = self.run_root / f"{stamp}-{request_id}-ranking"
        run_dir.mkdir(parents=True, exist_ok=True)
        _write_json(run_dir / "data_sources.json", {
            "schema_version": "4.0", "request_id": request_id,
            "profile": {"matched": bool(profile), "profile_id": getattr(profile, "profile_id", None)},
            "jd": {"jd_id": jd.get("jd_id"), "authority": "REQUEST_SPECIFIC"},
            "resume": {"backend": "mysql", "count": resume_count, "candidate_pool_count": len(pool)},
        })
        _write_json(run_dir / "request_snapshot.json", request)
        emit_progress("RESUME_FETCH", f"已建立 {len(pool)} 人候选池，开始完整语义评分…", current=0, total=len(pool))
        results = []
        failures = []
        workers = max(1, int(os.environ.get("RECRUITER_SCORING_WORKERS", "3")))

        def one(idx_resume):
            idx, resume = idx_resume
            cid = str(resume.get("candidate_id") or resume.get("resume_id") or f"candidate-{idx}")
            try:
                score = score_candidate_v4(
                    resume, request_id=request_id, jd=jd, profile=getattr(profile, "raw", None) if profile else None,
                    evaluator=self.evaluator, trace_dir=run_dir / "candidates" / cid,
                )
                return idx, score, None
            except Exception as exc:
                failure = {"candidate_id": cid, "name": resume.get("name"), "scoring_status": "FAILED", "error": str(exc)}
                _write_json(run_dir / "candidates" / cid / "score_result.json", failure)
                return idx, None, failure

        completed = 0
        with ThreadPoolExecutor(max_workers=min(workers, max(1, len(pool)))) as executor:
            futures = [executor.submit(one, item) for item in enumerate(pool, 1)]
            for future in as_completed(futures):
                _, score, failure = future.result()
                if score is not None:
                    results.append(score)
                if failure is not None:
                    failures.append(failure)
                completed += 1
                emit_progress("SEMANTIC_EVIDENCE", f"正在评估候选人 {completed}/{len(pool)}…", current=completed, total=len(pool))
        eligible = [x for x in results if x.get("scoring_status") == "SCORING_COMPLETE" and not x.get("veto")]
        eligible.sort(key=lambda x: (float(x.get("cs") or -1), float((x.get("jm") or {}).get("score") or -1), float((x.get("rq") or {}).get("score") or -1)), reverse=True)
        ranking = []
        for rank, item in enumerate(eligible, 1):
            ranking.append({
                "rank": rank, "candidate_id": item["candidate_id"], "name": item.get("name"),
                "cs": item.get("cs"), "rq": (item.get("rq") or {}).get("score"), "jm": (item.get("jm") or {}).get("score"),
                "profile_match": (item.get("profile_match") or {}).get("score") if item.get("profile_match") else None,
                "jd_match": (item.get("jd_match") or {}).get("score"),
                "needs_verification": item.get("needs_verification") or [], "strengths": item.get("strengths") or [],
                "summary": item.get("candidate_summary") or {},
            })
        snapshot = {
            "schema_version": "4.0", "request_id": request_id, "resume_count": resume_count,
            "candidate_pool_count": len(pool), "scored_count": len(results), "failed_count": len(failures),
            "ranking": ranking, "failures": failures,
        }
        _write_json(run_dir / "ranking_snapshot.json", snapshot)
        emit_progress("DONE", f"候选池评分完成：{len(ranking)} 人进入可推荐排序。", current=len(pool), total=len(pool), status="done")
        return {"resume_count": resume_count, "ranking": ranking, "trace_dir": str(run_dir), "failures": failures}
