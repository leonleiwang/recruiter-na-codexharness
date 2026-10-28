#!/usr/bin/env python3
"""One safe live LLM call proving semantic (not lexical) evidence behavior."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from rn.resume_screening.scoring.screening_pipeline_v3 import screen_candidate_v3  # noqa: E402
from rn.resume_screening.decision.semantic_evidence_evaluator import SemanticEvidenceEvaluator  # noqa: E402

job = {
    "id": "SMOKE-JOB",
    "title": "平台稳定性工程师",
    "location": "杭州",
    "hard_requirements": [{"name": "地点", "value": "杭州", "verify_type": "hard"}],
    "portrait_tags": [{"category": "capability", "tags": [
        {"name": "高可用架构设计与治理", "weight": 0.5},
        {"name": "Kubernetes 平台深度治理", "weight": 0.5},
    ]}],
}
resume = {
    "resume_id": "SMOKE-CANDIDATE",
    "name": "语义烟测候选人",
    "education": {"level": "本科"},
    "location": "上海",
    "source": "synthetic_smoke",
    "created_at": "2026-09-04",
    # Deliberately does NOT contain the tag name "高可用架构设计与治理".
    "raw_text": "负责 Keepalived 双机部署，通过 VIP 自动漂移实现主节点故障后的服务切换，并组织故障复盘。技能：Kubernetes。现居上海。",
    "work_experiences": [{
        "company": "示例公司", "position": "平台工程师", "start_date": "2022-01", "end_date": "至今",
        "description": "负责 Keepalived 双机部署，通过 VIP 自动漂移实现主节点故障后的服务切换，并组织故障复盘。",
    }],
}
out_dir = ROOT / "workspace" / "runs" / "semantic-smoke"
result = screen_candidate_v3(resume, job, evaluator=SemanticEvidenceEvaluator.from_env(), trace_dir=out_dir, as_of="2026-09-04")
print(json.dumps(result, ensure_ascii=False, indent=2))
if result.get("scoring_status") != "SCORING_COMPLETE":
    raise SystemExit(2)
levels = {item["tag_id"]: item["evidence_level"] for item in result["jm"]["tag_details"]}
semantic_level = levels.get("T01")
keyword_only_level = levels.get("T02")
if semantic_level not in {"CASE", "QUANTIFIED_CASE"}:
    print(f"Semantic smoke failed: expected CASE/QUANTIFIED_CASE without tag keyword, got {semantic_level}", file=sys.stderr)
    raise SystemExit(3)
if keyword_only_level not in {"WEAK", "NO_EVIDENCE"}:
    print(f"Semantic smoke failed: keyword-only Kubernetes evidence was over-scored as {keyword_only_level}", file=sys.stderr)
    raise SystemExit(5)
if result.get("veto"):
    print("Semantic smoke failed: current Shanghai location was incorrectly vetoed for Hangzhou", file=sys.stderr)
    raise SystemExit(4)
print("SEMANTIC_SMOKE_PASS")
