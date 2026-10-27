"""LLM semantic evidence evaluator for Recruiter-Na v3.1.

The evaluator is the semantic authority. It returns evidence contracts only;
it never calculates RQ/JM/CS. Any request/JSON/contract failure raises instead
of falling back to lexical matching.

Supports both Anthropic-style Responses API and OpenAI-compatible Chat
Completions API. Set RECRUITER_LLM_API_MODE=chat for OpenAI-compatible
endpoints (e.g. Alibaba Cloud MaaS).

Model fallback: set RECRUITER_LLM_FALLBACK_MODELS as a comma-separated list
of backup models. On 429/5xx, the evaluator cycles through them in order
before giving up.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable


class EvidenceEvaluationError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def extract_response_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    pieces: list[str] = []
    for output in payload.get("output") or []:
        if not isinstance(output, dict):
            continue
        for content in output.get("content") or []:
            if isinstance(content, dict) and content.get("type") in {"output_text", "text"} and content.get("text"):
                pieces.append(str(content["text"]))
    if pieces:
        return "\n".join(pieces).strip()
    # A few compatible gateways wrap a completion-like message.
    choices = payload.get("choices") or []
    if choices and isinstance(choices[0], dict):
        message = choices[0].get("message") or {}
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return message["content"].strip()
    raise EvidenceEvaluationError("LLM_OUTPUT_MISSING", "Responses API payload contained no output text")


def _requests_post_json_once(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - deployment dependency check
        raise EvidenceEvaluationError("REQUESTS_NOT_INSTALLED", "pip install requests") from exc
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        raise EvidenceEvaluationError("LLM_REQUEST_RETRYABLE", str(exc)) from exc
    if response.status_code == 429 or response.status_code >= 500:
        raise EvidenceEvaluationError("LLM_HTTP_RETRYABLE", f"HTTP {response.status_code}: {response.text[:500]}")
    if response.status_code >= 400:
        raise EvidenceEvaluationError("LLM_HTTP_ERROR", f"HTTP {response.status_code}: {response.text[:500]}")
    try:
        return response.json()
    except ValueError as exc:
        raise EvidenceEvaluationError("LLM_RESPONSE_NOT_JSON", response.text[:500]) from exc


def post_json_with_retry(
    post_once: Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]],
    url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float,
    *, max_attempts: int = 5, sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    attempts = max(1, int(max_attempts))
    for attempt in range(1, attempts + 1):
        try:
            return post_once(url, headers, payload, timeout)
        except EvidenceEvaluationError as exc:
            retryable = exc.code in {"LLM_HTTP_RETRYABLE", "LLM_REQUEST_RETRYABLE"}
            if not retryable or attempt >= attempts:
                raise
            sleep_fn(min(30, 2 ** (attempt - 1)))
    raise EvidenceEvaluationError("LLM_REQUEST_FAILED", "retry loop exhausted")


def _default_post_json(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    return post_json_with_retry(_requests_post_json_once, url, headers, payload, timeout)


def _responses_url(base_url: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise EvidenceEvaluationError("LLM_CONFIG_MISSING", "RECRUITER_LLM_BASE_URL is required")
    return base if base.endswith("/responses") else base + "/responses"


def _chat_completions_url(base_url: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        raise EvidenceEvaluationError("LLM_CONFIG_MISSING", "RECRUITER_LLM_BASE_URL is required")
    return base if base.endswith("/chat/completions") else base + "/chat/completions"


def _build_prompt(job_contract: dict[str, Any], resume_facts: dict[str, Any]) -> str:
    return f"""你是招聘语义证据评估器，不是打分器。你的唯一任务是把岗位合同与候选人简历事实转换成 Semantic Evidence Contract JSON。

硬规则：
1. 不得使用纯关键词命中/缺失来决定证据等级。关键词相同但没有具体行为，最多 WEAK；没有标签原词但存在同义的真实行为案例，可以 CASE/QUANTIFIED_CASE。
2. 不得输出 RQ、JM、CS、总分、排名或录用概率。
3. 每个岗位 tag 必须恰好输出一个 assessment，等级只能是 QUANTIFIED_CASE / CASE / WEAK / NO_EVIDENCE / UNASSESSABLE。
4. QUANTIFIED_CASE/CASE/WEAK 的 evidence.text 必须逐字引用候选人事实中的原文，不得改写、拼接或编造；同时给 source_path（例如 raw_text、work_experiences[0].description、certifications[0]）。
5. requirement status 只能 PASS / FAIL / UNKNOWN。缺失信息绝不是 FAIL。
6. 工作地点硬条件：当前城市不同、期望城市不同或未写意愿都必须 UNKNOWN；只有候选人明确表示不接受目标地点/现场办公等直接冲突事实才可 FAIL。
7. HARD_GATE 是否 veto 由下游确定性引擎处理，你这里只判断事实状态。
8. 忽略性别、年龄、婚育、民族、宗教、户籍、健康、犯罪记录等敏感属性，不得用它们支持任何 assessment。
9. JSON 之外不要输出任何文字。

输出结构：
{{
  "schema_version":"3.0",
  "candidate_id":"...",
  "job_id":"...",
  "evaluation_status":"COMPLETE",
  "tag_assessments":[{{
    "tag_id":"T01",
    "evidence_level":"CASE",
    "confidence":0.0,
    "evidence":[{{"text":"逐字原文","source_path":"raw_text"}}],
    "reasoning_summary":"简短语义判断"
  }}],
  "requirement_assessments":[{{
    "requirement_id":"R01",
    "status":"UNKNOWN",
    "evidence":[],
    "reasoning_summary":"简短事实判断"
  }}]
}}

JOB_CONTRACT:
{json.dumps(job_contract, ensure_ascii=False, separators=(',', ':'))}

RESUME_FACTS:
{json.dumps(resume_facts, ensure_ascii=False, separators=(',', ':'))}
"""


def _build_chat_messages(job_contract: dict[str, Any], resume_facts: dict[str, Any]) -> list[dict[str, str]]:
    return [{"role": "user", "content": _build_prompt(job_contract, resume_facts)}]


@dataclass
class SemanticEvidenceEvaluator:
    base_url: str
    model: str
    api_key: str
    timeout_seconds: float = 60.0
    post_json: Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]] = _default_post_json
    max_resume_chars: int = 120_000
    api_mode: str = "responses"  # "responses" or "chat"
    fallback_models: list[str] | None = None  # ordered list of backup model names

    @classmethod
    def from_env(cls) -> "SemanticEvidenceEvaluator":
        key = os.environ.get("RECRUITER_LLM_API_KEY", "").strip()
        if not key:
            env_name = os.environ.get("RECRUITER_LLM_API_KEY_ENV", "").strip()
            if env_name:
                key = os.environ.get(env_name, "").strip()
        fallback_raw = os.environ.get("RECRUITER_LLM_FALLBACK_MODELS", "").strip()
        fallback = [m.strip() for m in fallback_raw.split(",") if m.strip()] if fallback_raw else None
        api_mode = os.environ.get("RECRUITER_LLM_API_MODE", "responses").strip().lower()
        if api_mode not in ("responses", "chat"):
            api_mode = "responses"
        return cls(
            base_url=os.environ.get("RECRUITER_LLM_BASE_URL", ""),
            model=os.environ.get("RECRUITER_LLM_MODEL", ""),
            api_key=key,
            timeout_seconds=float(os.environ.get("RECRUITER_LLM_TIMEOUT_SECONDS", "60")),
            max_resume_chars=int(os.environ.get("RECRUITER_MAX_RESUME_CHARS", "120000")),
            api_mode=api_mode,
            fallback_models=fallback,
        )

    def _build_url(self) -> str:
        if self.api_mode == "chat":
            return _chat_completions_url(self.base_url)
        return _responses_url(self.base_url)

    def _build_payload(self, model: str, job_contract: dict[str, Any], resume_facts: dict[str, Any]) -> dict[str, Any]:
        if self.api_mode == "chat":
            return {"model": model, "messages": _build_chat_messages(job_contract, resume_facts)}
        return {"model": model, "input": _build_prompt(job_contract, resume_facts)}

    def _try_evaluate_with_model(
        self, model: str, url: str, headers: dict[str, str], job_contract: dict[str, Any], resume_facts: dict[str, Any],
    ) -> dict[str, Any]:
        payload = self._build_payload(model, job_contract, resume_facts)
        response_payload = self.post_json(url, headers, payload, self.timeout_seconds)
        text = extract_response_text(response_payload)
        # Some models still wrap JSON in a Markdown fence despite the instruction.
        stripped = text.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            stripped = "\n".join(lines).strip()
        try:
            doc = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise EvidenceEvaluationError("LLM_INVALID_JSON", stripped[:500]) from exc
        if not isinstance(doc, dict):
            raise EvidenceEvaluationError("LLM_CONTRACT_INVALID", "semantic evidence must be a JSON object")
        if doc.get("candidate_id") != resume_facts.get("candidate_id") or doc.get("job_id") != job_contract.get("job_id"):
            raise EvidenceEvaluationError("LLM_CONTRACT_ID_MISMATCH", "candidate_id/job_id mismatch")
        if doc.get("evaluation_status") != "COMPLETE":
            raise EvidenceEvaluationError("LLM_EVALUATION_INCOMPLETE", str(doc.get("evaluation_status")))
        return doc

    def evaluate(self, job_contract: dict[str, Any], resume_facts: dict[str, Any]) -> dict[str, Any]:
        if not self.model.strip():
            raise EvidenceEvaluationError("LLM_CONFIG_MISSING", "RECRUITER_LLM_MODEL is required")
        if not self.api_key.strip():
            raise EvidenceEvaluationError("LLM_CONFIG_MISSING", "semantic evaluator API key is required")
        raw_text = str(resume_facts.get("raw_text") or "")
        if len(raw_text) > self.max_resume_chars:
            raise EvidenceEvaluationError("RESUME_TEXT_TOO_LARGE", f"raw_text has {len(raw_text)} chars; max={self.max_resume_chars}")
        url = self._build_url()
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        # Build ordered model list: primary + fallback chain
        models = [self.model.strip()]
        if self.fallback_models:
            models.extend([m.strip() for m in self.fallback_models if m.strip()])

        last_error: Exception | None = None
        for idx, model in enumerate(models):
            try:
                return self._try_evaluate_with_model(model, url, headers, job_contract, resume_facts)
            except EvidenceEvaluationError as exc:
                last_error = exc
                # On 429 or 5xx, try next model in the chain
                if exc.code in {"LLM_HTTP_RETRYABLE", "LLM_REQUEST_RETRYABLE"} and idx < len(models) - 1:
                    time.sleep(1.0)  # brief pause before switching models
                    continue
                raise
            except Exception as exc:
                last_error = exc
                raise EvidenceEvaluationError("LLM_REQUEST_FAILED", str(exc)) from exc

        raise EvidenceEvaluationError("LLM_REQUEST_FAILED", str(last_error)) from last_error
