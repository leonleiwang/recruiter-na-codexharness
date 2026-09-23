#!/usr/bin/env python3
"""本地简历评估回归：PDF -> resume facts -> 语义证据 -> 确定性决策。

不依赖 MySQL 简历库，直接评估单份简历，用于打磨"推给用人经理的人是否正确"。

用法：
  python scripts/eval-resumes.py --resume-dir "<含 PDF 的目录>"
  python scripts/eval-resumes.py --resume-dir DIR --truth "邓嘉欣=淘汰,李静文=入职"
  python scripts/eval-resumes.py --resume-dir DIR --json workspace/eval-out.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

# Windows 控制台默认 GBK，报告里的符号会抛 UnicodeEncodeError。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def load_env_file(path: Path) -> None:
    """加载 KEY=VALUE 形式的本地配置（不覆盖已有环境变量）。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        i = line.find("=")
        if i < 1:
            continue
        key, value = line[:i].strip(), line[i + 1:].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


load_env_file(ROOT / ".env.local")

from rn.candidate_decision_v5 import decide_candidate  # noqa: E402
from rn.criteria_adapter_v5 import build_actual_criteria  # noqa: E402
from rn.decision_evidence_evaluator import DecisionEvidenceEvaluator  # noqa: E402
from rn.job_contract_v3 import normalize_resume_facts  # noqa: E402


def read_document_text(path: Path) -> str:
    """PDF 用 PyMuPDF；DOCX 用 python-docx；其它类型返回空（由调用方标记跳过）。"""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        import fitz
        with fitz.open(path) as doc:
            return "\n".join(page.get_text() for page in doc).strip()
    if suffix == ".docx":
        import docx
        return "\n".join(p.text for p in docx.Document(str(path)).paragraphs).strip()
    return ""


def load_profile(profile_id: str):
    """从画像库按 profile_id 取出画像对象（应届/社招口径不同）。"""
    if not profile_id:
        return None
    from rn.repositories import ProfileRepository
    repo = ProfileRepository()
    profiles = list(repo.list_all()) if hasattr(repo, "list_all") else list(repo.all())
    return next((p for p in profiles if str(getattr(p, "profile_id", "")) == profile_id), None)


def build_criteria(profile_id: str = "") -> dict:
    request = {
        "request_id": "REQ-eval",
        "request": {
            "fields": {"position_title": "检验技术员", "location": "杭州"},
            "profile_match": {"status": "MATCHED", "profile_id": profile_id},
        },
    }
    profile = load_profile(profile_id)
    if profile_id and profile is None:
        raise SystemExit("未找到画像: %s" % profile_id)
    return build_actual_criteria(request, profile=profile)


def evaluate_one(path: Path, criteria: dict, evaluator: DecisionEvidenceEvaluator) -> dict:
    raw_text = read_document_text(path)
    if len(raw_text) < 120:
        return {"resume": path.stem, "decision": "SKIPPED_NO_TEXT", "chars": len(raw_text),
                "reason": "无可解析文本层（可能是扫描件/图片简历），需要 OCR 才能评估"}
    facts = normalize_resume_facts({"candidate_id": path.stem, "name": path.stem, "raw_text": raw_text})
    evidence = evaluator.evaluate(criteria, facts)
    decision = decide_candidate(criteria, evidence, facts)
    return {
        "resume": path.stem,
        "decision": decision.get("decision"),
        "veto_items": [v.get("criterion") for v in decision.get("veto_items") or []],
        "fail_reasons": [f.get("criterion", "") + ":" + f.get("rule", "") for f in decision.get("fail_reasons") or []],
        "needs_verification": [v.get("item") for v in decision.get("needs_verification") or []],
        "interview_focus": [v.get("item") for v in decision.get("interview_focus") or []],
        "matched_tag_count": decision.get("matched_tag_count"),
        "applicable_tag_count": decision.get("applicable_tag_count"),
        "criteria": [
            {
                "id": r.get("criterion_id"),
                "result": r.get("criterion_result"),
                "evidence_status": r.get("evidence_status"),
                "mode": r.get("decision_mode"),
                "reason": (r.get("reasoning_summary") or "")[:120],
            }
            for r in evidence.get("criteria_assessments") or []
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--resume-dir", required=True)
    ap.add_argument("--truth", default="", help='形如 "邓嘉欣=淘汰,李静文=入职"')
    ap.add_argument("--json", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4, help="并发评估数")
    ap.add_argument("--ext", default="pdf,docx", help="参与评估的扩展名")
    ap.add_argument("--profile-id", default="", help="按画像口径评估（应届/社招判据不同）")
    args = ap.parse_args()

    # 业务标注 -> v5 决策词表：入职=pass，淘汰=fail
    LABEL_MAP = {"入职": "pass", "录用": "pass", "通过": "pass", "淘汰": "fail", "不通过": "fail", "fail": "fail", "pass": "pass"}
    truth = {}
    for pair in [p for p in args.truth.split(",") if "=" in p]:
        name, want = pair.split("=", 1)
        truth[name.strip()] = LABEL_MAP.get(want.strip(), want.strip())

    criteria = build_criteria(args.profile_id)
    evaluator = DecisionEvidenceEvaluator.from_env()
    c06 = next((c for c in criteria.get("criteria") or [] if c.get("name") == "检验科经验"), None)
    print("模型:", os.environ.get("RECRUITER_LLM_MODEL"), "| policy:", criteria.get("policy_id"))
    print("口径: profile_id=%s | 检验科经验=%s/%s" % (
        args.profile_id or "(policy 默认=社招)",
        (c06 or {}).get("level"), (c06 or {}).get("decision_mode")))

    exts = {"." + e.strip().lower().lstrip(".") for e in args.ext.split(",") if e.strip()}
    files = sorted(p for p in Path(args.resume_dir).iterdir() if p.is_file() and p.suffix.lower() in exts)
    if args.limit:
        files = files[: args.limit]
    if not files:
        print("目录里没有 PDF:", args.resume_dir)
        return 2

    results: list[dict] = []
    verbose = len(files) <= 12
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(evaluate_one, path, criteria, evaluator): path for path in files}
        for done, future in enumerate(as_completed(futures), 1):
            path = futures[future]
            try:
                out = future.result()
            except Exception as exc:  # 单份失败不影响整批
                out = {"resume": path.stem, "decision": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
            out["file"] = path.name
            results.append(out)
            print("[%d/%d] %s -> %s" % (done, len(files), path.stem, out.get("decision")), flush=True)
            if not verbose:
                continue
            want = truth.get(path.stem)
            mark = ""
            if want:
                mark = " 与标注一致" if out.get("decision") == want else " 标注=%s" % want
            print("   否决项:", out.get("veto_items") or "无", "| 待核实:", out.get("needs_verification") or "无", mark)
            for c in out.get("criteria", []):
                flag = "  <-- not_satisfied" if c["result"] == "not_satisfied" else ""
                print("      %-6s %-16s %-10s%s" % (c["id"], c["result"], c["evidence_status"], flag))

    tally: dict[str, int] = {}
    for r in results:
        tally[r.get("decision")] = tally.get(r.get("decision"), 0) + 1
    print("\n=== 汇总 ===")
    for k in sorted(tally, key=lambda x: -tally[x]):
        print("  %-16s %d" % (k, tally[k]))
    vetoed = [r for r in results if r.get("veto_items")]
    if vetoed:
        print("  被否决的候选人（%d 人）:" % len(vetoed))
        for r in vetoed[:20]:
            print("    %-30s 否决项=%s" % (r["resume"], ",".join(r["veto_items"])))
    print("  文本不足而跳过:", tally.get("SKIPPED_NO_TEXT", 0), "| 评估失败:", tally.get("ERROR", 0))

    if args.json:
        target = Path(args.json)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"policy_id": criteria.get("policy_id"), "results": results},
                                     ensure_ascii=False, indent=2), encoding="utf-8")
        print("\n结果已写入:", target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())