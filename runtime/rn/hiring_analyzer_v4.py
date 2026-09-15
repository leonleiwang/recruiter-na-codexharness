"""Semantic requirement intake and standard-profile matching for hiring managers.

Profile selection is always LLM-semantic. Deterministic parsing is allowed only
for literal request fields (for example, an explicit headcount or salary); it
never decides that a standard profile matches.
"""
from __future__ import annotations

import json
import os
import re
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


def _minimal_literal_fields(text: str) -> tuple[dict[str, Any], list[str]]:
    """Extract only literal fields. This must never infer profile membership."""
    t = str(text or "")
    fields: dict[str, Any] = {}
    provided: list[str] = []
    m = re.search(r"(?:招一个|招个|招聘一个|招聘|需要一个|要一个)\s*([^，,。；;！？!?]{2,24})", t)
    if m:
        title = m.group(1).strip()
        title = re.sub(r"(?:，|,).*", "", title)
        fields["position_title"] = title
        provided.append("position_title")
    m = re.search(r"(?:HC|hc|人数|招)\s*[:：]?\s*(\d+)\s*(?:人|个)?", t)
    if m:
        fields["headcount"] = int(m.group(1)); provided.append("headcount")
    m = re.search(r"(\d+(?:\.\d+)?\s*[-~—至]\s*\d+(?:\.\d+)?\s*[KkWw万]?)", t)
    if m:
        fields["salary_range"] = m.group(1).strip(); provided.append("salary_range")
    for city in ("杭州", "上海", "南京", "北京", "广州", "深圳", "苏州", "沈阳", "武汉", "成都"):
        if city in t:
            fields["location"] = city; provided.append("location"); break
    if any(x in t for x in ("尽快", "紧急", "急招")):
        fields["urgency"] = "尽快"; provided.append("urgency")
    return fields, provided


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
5. 输出 JSON 之外不要输出文字。

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
            fields, provided = _minimal_literal_fields(text)
            return {
                "fields": fields,
                "provided_fields": provided,
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
            # Conservative fallback: literal parser only. It does not infer missing semantic fields.
            fields, provided = _minimal_literal_fields(text)
            if len(missing_fields) == 1 and str(text).strip() in {"没有", "无", "没有了", "无额外要求", "没有额外要求"}:
                fields[missing_fields[0]] = []
                provided.append(missing_fields[0])
            return {"fields": fields, "provided_fields": provided}
