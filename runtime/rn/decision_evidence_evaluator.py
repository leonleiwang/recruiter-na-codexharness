"""Strict semantic evidence evaluator for Recruiter-Na v5 Candidate Decision.

The model is limited to fact interpretation and grounded evidence. It never
outputs the final candidate decision or any numerical candidate score.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable

from rn.semantic_evidence_evaluator import (
    EvidenceEvaluationError,
    _chat_completions_url,
    _requests_post_json_once,
    _responses_url,
    extract_response_text,
    post_json_with_retry,
)

_CRITERION_RESULTS = {"satisfied", "not_satisfied", "unknown"}
_EVIDENCE_STATUS = {"confirmed", "partial", "none"}
_HARD_STATUS = {
    "education": {"matched", "not_matched", "unknown"},
    "certification": {"valid", "pending_exam", "expired", "none", "unknown"},
    "location": {"match", "willing", "unknown", "refused"},
}
_RED_LINE_STATUS = {"hit", "clear", "unknown"}


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float, minimum: float = 0.1) -> float:
    try:
        return max(minimum, float(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def _split_models(raw: str) -> list[str]:
    return [x.strip() for x in str(raw or "").split(",") if x.strip()]


def _prefer_fast_fallbacks(models: list[str]) -> list[str]:
    """Prefer explicitly latency-oriented fallbacks while preserving stable order."""
    fast = [m for m in models if "flash" in m.casefold() or "fast" in m.casefold()]
    rest = [m for m in models if m not in fast]
    return [*fast, *rest]



def _prefer_interactive_qwen(models: list[str]) -> list[str]:
    """Prefer the low-latency Qwen models validated for Candidate Decision."""
    order = ("qwen3.6-flash", "qwen3.7-max")
    preferred = [name for name in order if name in models]
    rest = [m for m in models if m not in preferred]
    return [*preferred, *rest]

def _default_decision_post_json(
    url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float
) -> dict[str, Any]:
    """Interactive Candidate Decision HTTP policy: fail fast, then switch model."""
    attempts = _env_int("RECRUITER_DECISION_MAX_ATTEMPTS", 1)
    return post_json_with_retry(
        _requests_post_json_once, url, headers, payload, timeout, max_attempts=attempts
    )


def _evidence_list(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise EvidenceEvaluationError("LLM_CONTRACT_INVALID", "evidence must be a list")
    out: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            raise EvidenceEvaluationError("LLM_CONTRACT_INVALID", "evidence item must be object")
        text = str(item.get("text") or "").strip()
        source_path = str(item.get("source_path") or item.get("source") or "").strip()
        if not text:
            raise EvidenceEvaluationError("LLM_CONTRACT_INVALID", "evidence text must not be empty")
        out.append({"text": text, "source_path": source_path})
    return out


def validate_decision_evidence_contract(criteria: dict[str, Any], resume_facts: dict[str, Any], doc: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(doc, dict):
        raise EvidenceEvaluationError("LLM_CONTRACT_INVALID", "decision evidence must be an object")
    if doc.get("evaluation_status") != "COMPLETE":
        raise EvidenceEvaluationError("LLM_EVALUATION_INCOMPLETE", str(doc.get("evaluation_status")))
    if str(doc.get("candidate_id") or "") != str(resume_facts.get("candidate_id") or ""):
        raise EvidenceEvaluationError("LLM_CONTRACT_ID_MISMATCH", "candidate_id mismatch")
    if str(doc.get("policy_id") or "") != str(criteria.get("policy_id") or ""):
        raise EvidenceEvaluationError("LLM_CONTRACT_ID_MISMATCH", "policy_id mismatch")

    # 契约修复记录：无逐字证据的"确定性结论"一律降级，绝不让整份候选人评估失败。
    repairs: list[dict[str, Any]] = []

    hard_checks = doc.get("hard_checks")
    if not isinstance(hard_checks, dict):
        raise EvidenceEvaluationError("LLM_CONTRACT_INVALID", "hard_checks must be an object")
    for name, allowed in _HARD_STATUS.items():
        row = hard_checks.get(name)
        if not isinstance(row, dict) or row.get("status") not in allowed:
            raise EvidenceEvaluationError("LLM_CONTRACT_INVALID", f"invalid {name} status")
        evidence = _evidence_list(row.get("evidence") or [])
        status = row["status"]
        definitive = status != "unknown"
        if definitive and not evidence:
            repairs.append({"scope": f"hard_check:{name}", "from": status, "repair": "downgraded_to_unknown"})
            row["status"] = "unknown"
        row["evidence"] = evidence

    expected_red_lines = list((criteria.get("hard_filters") or {}).get("red_lines") or [])
    red_rows = doc.get("red_line_assessments") or []
    if not isinstance(red_rows, list) or len(red_rows) != len(expected_red_lines):
        raise EvidenceEvaluationError("LLM_CONTRACT_INVALID", "red line assessment count mismatch")
    expected_red_ids = [f"RL{i:02d}" for i in range(1, len(expected_red_lines) + 1)]
    seen_red: list[str] = []
    for row in red_rows:
        if not isinstance(row, dict) or row.get("status") not in _RED_LINE_STATUS:
            raise EvidenceEvaluationError("LLM_CONTRACT_INVALID", "invalid red line assessment")
        rid = str(row.get("red_line_id") or "")
        seen_red.append(rid)
        evidence = _evidence_list(row.get("evidence") or [])
        if row.get("status") == "hit" and not evidence:
            repairs.append({"scope": rid, "from": "hit", "repair": "downgraded_to_unknown"})
            row["status"] = "unknown"
        row["evidence"] = evidence
    if sorted(seen_red) == sorted(expected_red_ids) and seen_red != expected_red_ids:
        order = {rid: i for i, rid in enumerate(expected_red_ids)}
        doc["red_line_assessments"] = sorted(red_rows, key=lambda r: order[str(r.get("red_line_id") or "")])
        repairs.append({"scope": "red_line_assessments", "repair": "reordered"})
        seen_red = list(expected_red_ids)
    if seen_red != expected_red_ids:
        raise EvidenceEvaluationError("LLM_CONTRACT_INVALID", "red line ids mismatch")

    expected = [str(x.get("criterion_id") or "") for x in (criteria.get("criteria") or [])]
    rows = doc.get("criteria_assessments")
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise EvidenceEvaluationError("LLM_CONTRACT_INVALID", "criterion assessment count mismatch")
    seen: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            raise EvidenceEvaluationError("LLM_CONTRACT_INVALID", "criterion assessment must be object")
        cid = str(row.get("criterion_id") or "")
        seen.append(cid)
        if row.get("criterion_result") not in _CRITERION_RESULTS:
            raise EvidenceEvaluationError("LLM_CONTRACT_INVALID", f"invalid criterion_result for {cid}")
        if row.get("evidence_status") not in _EVIDENCE_STATUS:
            raise EvidenceEvaluationError("LLM_CONTRACT_INVALID", f"invalid evidence_status for {cid}")
        evidence = _evidence_list(row.get("evidence") or [])
        if row["criterion_result"] in {"satisfied", "not_satisfied"} and not evidence:
            repairs.append({"scope": cid, "from": row["criterion_result"], "repair": "downgraded_to_unknown"})
            row["criterion_result"] = "unknown"
            row["evidence_status"] = "none"
        elif row["evidence_status"] == "none" and evidence:
            repairs.append({"scope": cid, "from": "none_with_evidence", "repair": "upgraded_to_partial"})
            row["evidence_status"] = "partial"
        row["evidence"] = evidence
    if sorted(seen) == sorted(expected) and seen != expected:
        order = {cid: i for i, cid in enumerate(expected)}
        doc["criteria_assessments"] = sorted(rows, key=lambda r: order[str(r.get("criterion_id") or "")])
        repairs.append({"scope": "criteria_assessments", "repair": "reordered"})
        seen = list(expected)
    if seen != expected:
        raise EvidenceEvaluationError("LLM_CONTRACT_INVALID", "criterion ids/order mismatch")
    if repairs:
        doc["contract_repairs"] = repairs
    return doc


def _build_prompt(criteria: dict[str, Any], resume_facts: dict[str, Any]) -> str:
    return f"""你是 Recruiter-Na v5 的招聘语义事实评估器，不是打分器，也不是最终决策器。

你只负责把候选人简历事实映射为 Candidate Decision Evidence JSON。

必须遵守：
1. 禁止输出任何候选人分数、总分、排名、概率、RQ/JM/CS 或最终 pass/fail 决策。
2. 信息缺失永远不是不满足。没有写、关键词没搜到、语义不足，都返回 unknown。
3. criterion_result 与 evidence_status 独立判断：
   - criterion_result: satisfied / not_satisfied / unknown，回答“是否满足业务要求”。
   - evidence_status: confirmed / partial / none，回答“简历证据强弱”。
4. 只有明确事实证明不满足时才可 not_satisfied。比如要求≥1年，简历明确写实习6个月，可 not_satisfied；只写“相关经验”但年限不清必须 unknown。
5. hard check：
   - 学历: matched / not_matched / unknown。只有明确学历或专业冲突才 not_matched。
   - 证书: valid / pending_exam / expired / none / unknown。“未提及证书”必须 unknown；none 仅限明确表示无证。
   - 地点: match / willing / unknown / refused。当前城市不同不是 refused；只有明确拒绝目标地点才 refused。
   - 红线: hit / clear / unknown。hit 必须有直接事实证据；无法判断用 unknown。
6. 所有 matched/not_matched、valid/pending_exam/expired/none、match/willing/refused、red-line hit，以及 criterion_result satisfied/not_satisfied 的 evidence.text 必须逐字引用简历事实，不得改写或编造，并给 source_path。
7. confirmed 表示有具体、直接证据；partial 表示泛泛/间接/部分覆盖；none 表示无相关证据。
8. 忽略性别、年龄、婚育、民族、宗教、户籍、健康、犯罪记录等敏感属性，不得用于筛选。
9. 每个 hard check / red line / criterion 最多给 1 条最短且足够的逐字证据；不要重复引用同一段长文本。
10. reasoning_summary 保持极短，只说明事实判断，不展开推理过程。
11. JSON 之外不要输出任何文字。

输出结构：
{{
  "schema_version":"5.0",
  "candidate_id":"...",
  "policy_id":"...",
  "evaluation_status":"COMPLETE",
  "hard_checks":{{
    "education":{{"status":"matched|not_matched|unknown","evidence":[{{"text":"逐字原文","source_path":"raw_text"}}],"reasoning_summary":"..."}},
    "certification":{{"status":"valid|pending_exam|expired|none|unknown","evidence":[],"reasoning_summary":"..."}},
    "location":{{"status":"match|willing|unknown|refused","evidence":[],"reasoning_summary":"..."}}
  }},
  "red_line_assessments":[{{"red_line_id":"RL01","status":"hit|clear|unknown","evidence":[],"reasoning_summary":"..."}}],
  "criteria_assessments":[{{
    "criterion_id":"C01",
    "criterion_result":"satisfied|not_satisfied|unknown",
    "evidence_status":"confirmed|partial|none",
    "evidence":[{{"text":"逐字原文","source_path":"raw_text"}}],
    "reasoning_summary":"..."
  }}]
}}

ACTUAL_HIRING_CRITERIA:
{json.dumps(criteria, ensure_ascii=False, separators=(',', ':'))}

RESUME_FACTS:
{json.dumps(resume_facts, ensure_ascii=False, separators=(',', ':'))}
"""


@dataclass
class DecisionEvidenceEvaluator:
    base_url: str
    model: str
    api_key: str
    timeout_seconds: float = 15.0
    post_json: Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]] = _default_decision_post_json
    max_resume_chars: int = 120_000
    api_mode: str = "responses"
    fallback_models: list[str] | None = None
    max_attempts: int = 1
    max_models: int = 2

    @classmethod
    def from_env(cls) -> "DecisionEvidenceEvaluator":
        key = os.environ.get("RECRUITER_LLM_API_KEY", "").strip()
        if not key:
            env_name = os.environ.get("RECRUITER_LLM_API_KEY_ENV", "").strip()
            if env_name:
                key = os.environ.get(env_name, "").strip()
        dedicated_fallback_raw = os.environ.get("RECRUITER_DECISION_FALLBACK_MODELS", "").strip()
        legacy_fallback_raw = os.environ.get("RECRUITER_LLM_FALLBACK_MODELS", "").strip()
        api_mode = os.environ.get("RECRUITER_LLM_API_MODE", "responses").strip().lower()
        if api_mode not in {"responses", "chat"}:
            api_mode = "responses"
        explicit_model = os.environ.get("RECRUITER_DECISION_MODEL", "").strip()
        if dedicated_fallback_raw:
            fallbacks = _split_models(dedicated_fallback_raw)
            model = explicit_model or os.environ.get("RECRUITER_LLM_MODEL", "")
        else:
            legacy_models = _split_models(legacy_fallback_raw)
            if explicit_model:
                model = explicit_model
                fallbacks = _prefer_fast_fallbacks(legacy_models)
            else:
                interactive = _prefer_interactive_qwen(legacy_models)
                if interactive and interactive[0] in {"qwen3.6-flash", "qwen3.7-max"}:
                    model = interactive[0]
                    fallbacks = [m for m in interactive[1:] if m != model]
                else:
                    model = os.environ.get("RECRUITER_LLM_MODEL", "")
                    fallbacks = _prefer_fast_fallbacks(legacy_models)
        return cls(
            base_url=os.environ.get("RECRUITER_LLM_BASE_URL", ""),
            model=model,
            api_key=key,
            timeout_seconds=_env_float("RECRUITER_DECISION_TIMEOUT_SECONDS", 15.0),
            max_resume_chars=int(os.environ.get("RECRUITER_MAX_RESUME_CHARS", "120000")),
            api_mode=api_mode,
            fallback_models=fallbacks or None,
            max_attempts=_env_int("RECRUITER_DECISION_MAX_ATTEMPTS", 1),
            max_models=_env_int("RECRUITER_DECISION_MAX_MODELS", 2),
        )

    def _url(self) -> str:
        return _chat_completions_url(self.base_url) if self.api_mode == "chat" else _responses_url(self.base_url)

    def model_chain(self) -> list[str]:
        primary = self.model.strip()
        chain: list[str] = []
        for model in [primary, *list(self.fallback_models or [])]:
            value = str(model or "").strip()
            if value and value not in chain:
                chain.append(value)
            if len(chain) >= max(1, int(self.max_models)):
                break
        return chain

    def _payload(self, model: str, criteria: dict[str, Any], resume_facts: dict[str, Any]) -> dict[str, Any]:
        prompt = _build_prompt(criteria, resume_facts)
        # temperature=0：同一份简历必须得到同样的判据结论，否则决策规则会被采样噪声放大。
        if self.api_mode == "chat":
            return {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0}
        return {"model": model, "input": prompt, "temperature": 0}

    def _evaluate_model(self, model: str, criteria: dict[str, Any], resume_facts: dict[str, Any]) -> dict[str, Any]:
        if self.post_json is _default_decision_post_json:
            response = post_json_with_retry(
                _requests_post_json_once,
                self._url(),
                {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                self._payload(model, criteria, resume_facts),
                self.timeout_seconds,
                max_attempts=max(1, int(self.max_attempts)),
            )
        else:
            response = self.post_json(
                self._url(),
                {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                self._payload(model, criteria, resume_facts),
                self.timeout_seconds,
            )
        text = extract_response_text(response).strip()
        if text.startswith("```"):
            lines = text.splitlines()[1:]
            if lines and lines[-1].strip() == "```":
                lines.pop()
            text = "\n".join(lines).strip()
        try:
            doc = json.loads(text)
        except json.JSONDecodeError as exc:
            raise EvidenceEvaluationError("LLM_INVALID_JSON", text[:500]) from exc
        return validate_decision_evidence_contract(criteria, resume_facts, doc)

    def evaluate(self, criteria: dict[str, Any], resume_facts: dict[str, Any]) -> dict[str, Any]:
        if not self.model.strip():
            raise EvidenceEvaluationError("LLM_CONFIG_MISSING", "RECRUITER_LLM_MODEL is required")
        if not self.api_key.strip():
            raise EvidenceEvaluationError("LLM_CONFIG_MISSING", "semantic evaluator API key is required")
        raw_text = str(resume_facts.get("raw_text") or "")
        if len(raw_text) > self.max_resume_chars:
            raise EvidenceEvaluationError("RESUME_TEXT_TOO_LARGE", f"raw_text has {len(raw_text)} chars; max={self.max_resume_chars}")
        models = self.model_chain()
        last: Exception | None = None
        for idx, model in enumerate(models):
            try:
                return self._evaluate_model(model, criteria, resume_facts)
            except EvidenceEvaluationError as exc:
                last = exc
                if exc.code in {"LLM_HTTP_RETRYABLE", "LLM_REQUEST_RETRYABLE"} and idx < len(models) - 1:
                    time.sleep(0.25)
                    continue
                raise
        raise EvidenceEvaluationError("LLM_REQUEST_FAILED", str(last)) from last
