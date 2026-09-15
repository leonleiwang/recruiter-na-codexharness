"""Contract-first screening pipeline with mandatory semantic evidence."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from rn.evidence_grounding import validate_grounding
from rn.job_contract_v3 import build_job_contract, normalize_resume_facts
from rn.progress import emit_progress
from rn.scoring_engine_v3 import score_candidate
from rn.semantic_evidence_evaluator import EvidenceEvaluationError, SemanticEvidenceEvaluator

ENGINE_VERSION = "3.1.0"


class _MissingPrecomputedEvidenceEvaluator:
    def evaluate(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise EvidenceEvaluationError("PRECOMPUTED_EVIDENCE_MISSING", "precomputed evidence file not found")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _safe_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "candidate")).strip("._")
    return cleaned or "candidate"


def _failure(status: str, candidate_id: str, job_id: str, errors: list[str]) -> dict[str, Any]:
    return {
        "schema_version": "3.0",
        "engine_version": ENGINE_VERSION,
        "scoring_status": status,
        "candidate_id": candidate_id,
        "job_id": job_id,
        "errors": errors,
        "rq": None,
        "jm": None,
        "cs": None,
        "veto": False,
        "veto_reasons": [],
        "needs_verification": [],
        "recommendation_level": None,
    }


def _state(trace_dir: Path | None, candidate_id: str, state: str, transitions: list[str], errors: list[str] | None = None) -> None:
    if trace_dir is None:
        return
    _write_json(trace_dir / "state.json", {
        "engine_version": ENGINE_VERSION,
        "candidate_id": candidate_id,
        "state": state,
        "transitions": transitions,
        "errors": errors or [],
    })


def screen_candidate_v3(
    resume: dict[str, Any],
    job: dict[str, Any],
    *,
    evaluator: Any | None = None,
    semantic_evidence: dict[str, Any] | None = None,
    trace_dir: str | Path | None = None,
    as_of: str | None = None,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    trace = Path(trace_dir) if trace_dir is not None else None
    transitions: list[str] = []
    try:
        job_contract = build_job_contract(job, profile=profile)
        facts = normalize_resume_facts(resume)
    except Exception as exc:
        candidate_id = str(resume.get("candidate_id") or resume.get("resume_id") or "")
        result = _failure("PARSING_FAILED", candidate_id, str(job.get("job_id") or job.get("id") or ""), [str(exc)])
        if trace:
            _write_json(trace / "score_result.json", result)
            _state(trace, candidate_id, result["scoring_status"], transitions, result["errors"])
        return result

    candidate_id = facts["candidate_id"]
    job_id = job_contract["job_id"]
    transitions.append("RESUME_FACTS_READY")
    if trace:
        _write_json(trace / "resume_facts.json", facts)
        _state(trace, candidate_id, "RESUME_FACTS_READY", transitions)

    if semantic_evidence is None:
        emit_progress("SEMANTIC_EVIDENCE", f"正在评估候选人 {facts.get('name') or candidate_id} 的语义证据…")
        evaluator = evaluator or SemanticEvidenceEvaluator.from_env()
        try:
            semantic_evidence = evaluator.evaluate(job_contract, facts)
        except EvidenceEvaluationError as exc:
            failure_evidence = {
                "schema_version": "3.0", "candidate_id": candidate_id, "job_id": job_id,
                "evaluation_status": "FAILED", "error_code": exc.code, "error_message": str(exc),
            }
            result = _failure("EVIDENCE_EVALUATION_FAILED", candidate_id, job_id, [exc.code])
            if trace:
                _write_json(trace / "semantic_evidence.json", failure_evidence)
                _write_json(trace / "score_result.json", result)
                _state(trace, candidate_id, result["scoring_status"], transitions, result["errors"])
            return result
        except Exception as exc:  # fail closed for custom evaluator implementations
            result = _failure("EVIDENCE_EVALUATION_FAILED", candidate_id, job_id, ["LLM_EVALUATOR_ERROR", str(exc)])
            if trace:
                _write_json(trace / "semantic_evidence.json", {
                    "schema_version": "3.0", "candidate_id": candidate_id, "job_id": job_id,
                    "evaluation_status": "FAILED", "error_code": "LLM_EVALUATOR_ERROR",
                })
                _write_json(trace / "score_result.json", result)
                _state(trace, candidate_id, result["scoring_status"], transitions, result["errors"])
            return result

    transitions.append("SEMANTIC_EVIDENCE_READY")
    if trace:
        _write_json(trace / "semantic_evidence.json", semantic_evidence)
        _state(trace, candidate_id, "SEMANTIC_EVIDENCE_READY", transitions)

    emit_progress("EVIDENCE_GROUNDING", f"正在核验候选人 {facts.get('name') or candidate_id} 的证据出处…")
    grounding = validate_grounding(facts, semantic_evidence)
    if trace:
        _write_json(trace / "evidence_validation.json", grounding)
    if grounding["status"] != "GROUNDED":
        result = _failure("EVIDENCE_GROUNDING_FAILED", candidate_id, job_id, grounding["errors"])
        if trace:
            _write_json(trace / "score_result.json", result)
            _state(trace, candidate_id, result["scoring_status"], transitions, result["errors"])
        return result

    transitions.append("EVIDENCE_VALIDATED")
    emit_progress("SCORING", f"正在计算候选人 {facts.get('name') or candidate_id} 的 RQ / JM / CS…")
    result = score_candidate(facts, job_contract, semantic_evidence, as_of=as_of)
    result["engine_version"] = ENGINE_VERSION
    result["name"] = facts.get("name")
    if result["scoring_status"] == "SCORING_COMPLETE":
        transitions.append("SCORING_COMPLETE")
    if trace:
        _write_json(trace / "score_result.json", result)
        _state(trace, candidate_id, result["scoring_status"], transitions, result.get("errors") or [])
    return result


def _load_precomputed(evidence_dir: Path | None, candidate_id: str) -> dict[str, Any] | None:
    if evidence_dir is None:
        return None
    path = evidence_dir / f"{_safe_id(candidate_id)}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def screen_candidates_v3(
    resumes: list[dict[str, Any]],
    job: dict[str, Any],
    *,
    evaluator: Any | None = None,
    evidence_dir: str | Path | None = None,
    out_dir: str | Path | None = None,
    top: int = 3,
    as_of: str | None = None,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    emit_progress("JOB_CONTRACT", "正在读取岗位画像并生成评分合同…")
    job_contract = build_job_contract(job, profile=profile)
    out = Path(out_dir) if out_dir is not None else None
    pre_dir = Path(evidence_dir) if evidence_dir is not None else None
    if out:
        out.mkdir(parents=True, exist_ok=True)
        _write_json(out / "job_contract.json", job_contract)

    total_candidates = len(resumes)
    emit_progress("RESUME_FETCH", f"已载入 {total_candidates} 份候选简历，准备结构化解析…", current=0, total=total_candidates)
    results: list[dict[str, Any]] = []
    for index, resume in enumerate(resumes, 1):
        emit_progress("RESUME_PARSE", f"正在解析候选简历 {index}/{total_candidates}…", current=index, total=total_candidates)
        emit_progress("SEMANTIC_EVIDENCE", f"正在进行语义证据评估 {index}/{total_candidates}…", current=index, total=total_candidates)
        facts_id = str(resume.get("candidate_id") or resume.get("resume_id") or "candidate")
        evidence = _load_precomputed(pre_dir, facts_id)
        candidate_dir = out / "candidates" / _safe_id(facts_id) if out else None
        candidate_evaluator = evaluator
        if pre_dir is not None and evidence is None:
            candidate_evaluator = _MissingPrecomputedEvidenceEvaluator()
        result = screen_candidate_v3(
            resume, job_contract, evaluator=candidate_evaluator, semantic_evidence=evidence,
            trace_dir=candidate_dir, as_of=as_of,
        )
        results.append(result)

    emit_progress("FINALIZING", "正在生成候选人排名与筛选报告…")
    complete = [r for r in results if r.get("scoring_status") == "SCORING_COMPLETE"]
    non_veto = [r for r in complete if not r.get("veto")]
    veto_pool = [r for r in complete if r.get("veto")]
    failed = [r for r in results if r.get("scoring_status") != "SCORING_COMPLETE"]
    non_veto.sort(key=lambda r: (float(r.get("cs") or -1), float((r.get("jm") or {}).get("score") or -1), float((r.get("rq") or {}).get("score") or -1)), reverse=True)
    veto_pool.sort(key=lambda r: float((r.get("rq") or {}).get("score") or -1), reverse=True)
    for idx, item in enumerate(non_veto, 1):
        item["rank"] = idx

    report = {
        "meta": {
            "job_id": job_contract["job_id"],
            "job_name": job_contract.get("job_name"),
            "engine_version": ENGINE_VERSION,
            "total_candidates": len(results),
            "scoring_complete_count": len(complete),
            "failed_count": len(failed),
            "vetoed_count": len(veto_pool),
        },
        "job_contract": job_contract,
        "recommendations": non_veto[: max(1, int(top))],
        "veto_pool": veto_pool,
        "failed_candidates": failed,
    }
    if out:
        _write_json(out / "ranking.json", report)
        _write_json(out / "report.json", report)
        (out / "report.md").write_text(render_report_md(report), encoding="utf-8")
    emit_progress("DONE", f"筛选完成：{len(complete)}/{len(results)} 份形成正式评分。", current=len(results), total=len(results), status="done")
    return report


def render_report_md(report: dict[str, Any]) -> str:
    meta = report["meta"]
    lines = [
        f"# {meta.get('job_name') or meta.get('job_id')} 简历推荐报告（Recruiter-Na v3.4）",
        "",
        f"- 岗位编号：{meta.get('job_id')}",
        f"- 候选人数：{meta.get('total_candidates')}",
        f"- 完成语义证据并正式评分：{meta.get('scoring_complete_count')}",
        f"- 证据/合同失败：{meta.get('failed_count')}",
        f"- 明确硬条件否决：{meta.get('vetoed_count')}",
        "",
        "## Top 推荐",
        "",
        "| 排名 | 候选人 | CS | RQ | JM | 待核实 |",
        "|---:|---|---:|---:|---:|---|",
    ]
    for item in report.get("recommendations") or []:
        lines.append(
            f"| {item.get('rank')} | {item.get('name') or item.get('candidate_id')} | {item.get('cs')} | "
            f"{(item.get('rq') or {}).get('score')} | {(item.get('jm') or {}).get('score')} | "
            f"{', '.join(item.get('needs_verification') or []) or '-'} |"
        )
    if report.get("veto_pool"):
        lines += ["", "## 明确硬条件否决（HR 复核）", ""]
        for item in report["veto_pool"]:
            lines.append(f"- {item.get('name') or item.get('candidate_id')}: {', '.join(item.get('veto_reasons') or [])}")
    if report.get("failed_candidates"):
        lines += ["", "## 未形成正式分数", ""]
        for item in report["failed_candidates"]:
            lines.append(f"- {item.get('candidate_id')}: {item.get('scoring_status')} / {', '.join(item.get('errors') or [])}")
    lines += [
        "",
        "> 评分链：LLM 只输出可追溯语义证据；Python 只做确定性 RQ/JM/CS 计算。信息缺失=待核实，不等于不满足；最终面试/录用决定由 HR 与业务人员确认。",
    ]
    return "\n".join(lines) + "\n"
