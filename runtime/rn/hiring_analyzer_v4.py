"""Semantic requirement intake and standard-profile matching for hiring managers.

Profile selection and request-field extraction are performed by the configured
LLM.  This module deliberately has no keyword or regular-expression fallback.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from rn.semantic_evidence_evaluator import EvidenceEvaluationError, extract_response_text, post_json_with_retry, _requests_post_json_once


class HiringAnalysisError(RuntimeError):
    pass


def _json_from_text(text: str) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```"):
        lines = value.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        value = "\n".join(lines).strip()
    try:
        doc = json.loads(value)
    except json.JSONDecodeError as exc:
        raise HiringAnalysisError("HIRING_ANALYZER_INVALID_JSON") from exc
    if not isinstance(doc, dict):
        raise HiringAnalysisError("HIRING_ANALYZER_INVALID_CONTRACT")
    return doc


def _profile_catalog(profiles: list[Any]) -> list[dict[str, Any]]:
    out = []
    for record in profiles:
        raw = getattr(record, "raw", {}) or {}
        out.append({
            "profile_id": getattr(record, "profile_id", raw.get("profile_id")),
            "name": getattr(record, "name", raw.get("name")),
            "description": raw.get("description") or raw.get("business_challenge") or "",
            "abilities": [x.get("name") for x in raw.get("key_abilities") or [] if isinstance(x, dict)],
            "experiences": [x.get("name") for x in raw.get("key_experiences") or [] if isinstance(x, dict)],
        })
    return out


@dataclass
class HiringIntentAnalyzer:
    base_url: str
    model: str
    api_key: str
    timeout_seconds: float = 45.0
    api_mode: str = "responses"

    @classmethod
    def from_env(cls) -> "HiringIntentAnalyzer":
        key = os.environ.get("RECRUITER_LLM_API_KEY", "").strip()
        if not key:
            env_name = os.environ.get("RECRUITER_LLM_API_KEY_ENV", "").strip()
            if env_name:
                key = os.environ.get(env_name, "").strip()
        return cls(
            base_url=os.environ.get("RECRUITER_LLM_BASE_URL", ""),
            model=os.environ.get("RECRUITER_LLM_MODEL", ""),
            api_key=key,
            timeout_seconds=float(os.environ.get("RECRUITER_INTAKE_LLM_TIMEOUT_SECONDS", "45")),
            api_mode=os.environ.get("RECRUITER_LLM_API_MODE", "responses").strip().lower(),
        )

    def _call(self, prompt: str) -> dict[str, Any]:
        if not self.base_url or not self.model or not self.api_key:
            raise HiringAnalysisError("HIRING_ANALYZER_CONFIG_MISSING")
        base = self.base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        if self.api_mode == "chat":
            url = base if base.endswith("/chat/completions") else base + "/chat/completions"
            payload = {"model": self.model, "messages": [{"role": "user", "content": prompt}]}
        else:
            url = base if base.endswith("/responses") else base + "/responses"
            payload = {"model": self.model, "input": prompt}
        try:
            response = post_json_with_retry(_requests_post_json_once, url, headers, payload, self.timeout_seconds, max_attempts=3)
            return _json_from_text(extract_response_text(response))
        except (EvidenceEvaluationError, HiringAnalysisError) as exc:
            raise HiringAnalysisError(str(exc)) from exc

    def analyze_initial(self, text: str, profiles: list[Any]) -> dict[str, Any]:
        catalog = _profile_catalog(profiles)
        prompt = f"""你是企业招聘需求结构化分析器。请理解用人部门的一条自然语言招聘需求，并判断是否语义上属于给定的标准岗位画像。

严格规则：
1. 画像匹配必须基于岗位职责/专业能力/经验语义，不得仅因为岗位名称或关键词相同就 MATCHED。
2. 只有高度明确属于一个画像时 MATCHED；两者接近且缺关键区分信息时 AMBIGUOUS；不属于任何画像时 NO_MATCH。
3. 不得创造用户没有提供的招聘专属事实。标准画像只用于 profile_match，不要把画像内容写进 fields。
4. fields 只抽取用户明确提供的信息。provided_fields 只列用户明确给出的字段，即使用户明确说“没有额外硬要求”，也应列出 extra_hard_requirements 且值为 []。
5. 用户对工作经验层级、培养方式、岗位职责或技术方向的自然语言说明，都是画像区分依据。若这些说明已经能在候选画像中唯一确定一个，必须输出 MATCHED，不能再提出澄清问题或因为没有固定措辞而输出 AMBIGUOUS。
6. 输出 JSON 之外不要输出文字。

fields 可用字段：position_title, department, headcount, location, work_mode, salary_range, urgency, employment_type, extra_hard_requirements, core_responsibilities, hard_requirements, key_experience_skills, bonus_points。
列表字段：extra_hard_requirements, core_responsibilities, hard_requirements, key_experience_skills, bonus_points。

输出：
{{
  "fields": {{}},
  "provided_fields": [],
  "profile_match": {{
    "status": "MATCHED|AMBIGUOUS|NO_MATCH",
    "profile_id": "",
    "confidence": 0.0,
    "reason": "",
    "candidates": [],
    "clarifying_question": "仅 AMBIGUOUS 时给一个最能区分画像的问题"
  }}
}}

STANDARD_PROFILES:
{json.dumps(catalog, ensure_ascii=False)}

USER_REQUEST:
{text}
"""
        try:
            result = self._call(prompt)
            if result.get("profile_match", {}).get("status") not in {"MATCHED", "AMBIGUOUS", "NO_MATCH"}:
                raise HiringAnalysisError("HIRING_ANALYZER_PROFILE_STATUS_INVALID")
            return result
        except HiringAnalysisError:
            return {
                "fields": {},
                "provided_fields": [],
                "profile_match": {
                    "status": "UNAVAILABLE",
                    "profile_id": "",
                    "confidence": 0.0,
                    "reason": "语义画像匹配暂不可用，按无画像的完整需求采集继续，不使用关键词猜测。",
                    "candidates": [],
                },
            }

    def analyze_update(self, text: str, request: dict[str, Any], missing_fields: list[str]) -> dict[str, Any]:
        prompt = f"""你是招聘需求补充信息结构化器。已有招聘工单中仍缺少部分字段，请只从用户新消息中抽取明确提供的信息，不得脑补。

待补字段：{json.dumps(missing_fields, ensure_ascii=False)}
已有字段：{json.dumps((request.get('request') or {}).get('fields', request.get('fields', {})), ensure_ascii=False)}
用户新消息：{text}

输出 JSON：{{"fields":{{}},"provided_fields":[]}}
如果用户明确说“没有/无”来回答某个列表字段，该字段输出 [] 且必须出现在 provided_fields。
"""
        try:
            result = self._call(prompt)
            return {"fields": result.get("fields") or {}, "provided_fields": result.get("provided_fields") or []}
        except HiringAnalysisError:
            return {"fields": {}, "provided_fields": []}
