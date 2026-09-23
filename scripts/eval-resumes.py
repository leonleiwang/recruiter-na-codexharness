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


def read_pdf(path: Path) -> str:
    import fitz  # PyMuPDF
    with fitz.open(path) as doc:
        return "\n".join(page.get_text() for page in doc).strip()


def build_criteria() -> dict:
    request = {
        "request_id": "REQ-eval",
        "request": {
            "fields": {"position_title": "检验技术员", "location": "杭州"},
            "profile_match": {"status": "MATCHED"},
        },
    }
    return build_actual_criteria(request, profile=None)


def evaluate_one(path: Path, criteria: dict, evaluator: DecisionEvidenceEvaluator) -> dict:
    facts = normalize_resume_facts({"candidate_id": path.stem, "name": path.stem, "raw_text": read_pdf(path)})
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
    args = ap.parse_args()

    # 业务标注 -> v5 决策词表：入职=pass，淘汰=fail
    LABEL_MAP = {"入职": "pass", "录用": "pass", "通过": "pass", "淘汰": "fail", "不通过": "fail", "fail": "fail", "pass": "pass"}
    truth = {}
    for pair in [p for p in args.truth.split(",") if "=" in p]:
        name, want = pair.split("=", 1)
        truth[name.strip()] = LABEL_MAP.get(want.strip(), want.strip())

    criteria = build_criteria()
    evaluator = DecisionEvidenceEvaluator.from_env()
    print("模型:", os.environ.get("RECRUITER_LLM_MODEL"), "| policy:", criteria.get("policy_id"))

    files = sorted(Path(args.resume_dir).glob("*.pdf"))
    if args.limit:
        files = files[: args.limit]
    if not files:
        print("目录里没有 PDF:", args.resume_dir)
        return 2

    results = []
    for path in files:
        try:
            out = evaluate_one(path, criteria, evaluator)
        except Exception as exc:  # 单份失败不影响整批
            out = {"resume": path.stem, "decision": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
        results.append(out)
        want = truth.get(path.stem)
        mark = ""
        if want:
            mark = " ✅与标注一致" if out.get("decision") == want else f" ❌标注={want}"
        print("\n=== %s | 决策=%s%s" % (out["resume"], out.get("decision"), mark))
        if out.get("error"):
            print("    错误:", out["error"])
            continue
        print("    否决项:", out["veto_items"] or "无", "| 硬性失败:", out["fail_reasons"] or "无")
        print("    待核实:", out["needs_verification"] or "无")
        for c in out["criteria"]:
            flag = "  <-- not_satisfied" if c["result"] == "not_satisfied" else ""
            print("      %-6s %-16s %-10s %s%s" % (c["id"], c["result"], c["evidence_status"], c["mode"], flag))

    if args.json:
        target = Path(args.json)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"policy_id": criteria.get("policy_id"), "results": results},
                                     ensure_ascii=False, indent=2), encoding="utf-8")
        print("\n结果已写入:", target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
