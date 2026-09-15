import os

import pytest

import rn.decision_evidence_evaluator as module
from rn.decision_evidence_evaluator import DecisionEvidenceEvaluator
from rn.semantic_evidence_evaluator import EvidenceEvaluationError


def _base_env(monkeypatch):
    for key in (
        "RECRUITER_DECISION_MODEL",
        "RECRUITER_DECISION_FALLBACK_MODELS",
        "RECRUITER_DECISION_TIMEOUT_SECONDS",
        "RECRUITER_DECISION_MAX_ATTEMPTS",
        "RECRUITER_DECISION_MAX_MODELS",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("RECRUITER_LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("RECRUITER_LLM_MODEL", "deepseek-v4-pro")
    monkeypatch.setenv("RECRUITER_LLM_API_KEY", "secret")
    monkeypatch.setenv("RECRUITER_LLM_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv(
        "RECRUITER_LLM_FALLBACK_MODELS",
        "qwen3.7-max,deepseek-v4-flash,qwen3.7-plus,qwen3.6-flash",
    )


def test_decision_evaluator_uses_interactive_defaults_independent_of_global_timeout(monkeypatch):
    _base_env(monkeypatch)
    evaluator = DecisionEvidenceEvaluator.from_env()
    assert evaluator.timeout_seconds == 15.0
    assert evaluator.max_attempts == 1
    assert evaluator.max_models == 2


def test_decision_model_chain_prefers_qwen_interactive_models_when_no_dedicated_model(monkeypatch):
    _base_env(monkeypatch)
    evaluator = DecisionEvidenceEvaluator.from_env()
    assert evaluator.model_chain() == ["qwen3.6-flash", "qwen3.7-max"]


def test_dedicated_decision_model_and_fallbacks_override_legacy_chain(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("RECRUITER_DECISION_MODEL", "qwen-fast")
    monkeypatch.setenv("RECRUITER_DECISION_FALLBACK_MODELS", "fallback-fast,fallback-slow")
    monkeypatch.setenv("RECRUITER_DECISION_MAX_MODELS", "2")
    evaluator = DecisionEvidenceEvaluator.from_env()
    assert evaluator.model_chain() == ["qwen-fast", "fallback-fast"]


def test_default_decision_post_respects_single_attempt_budget(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("RECRUITER_DECISION_MAX_ATTEMPTS", "1")
    calls = {"count": 0}

    def always_timeout(url, headers, payload, timeout):
        calls["count"] += 1
        raise EvidenceEvaluationError("LLM_REQUEST_RETRYABLE", "timeout")

    monkeypatch.setattr(module, "_requests_post_json_once", always_timeout)
    with pytest.raises(EvidenceEvaluationError) as exc:
        module._default_decision_post_json("https://example.test", {}, {}, 15.0)
    assert exc.value.code == "LLM_REQUEST_RETRYABLE"
    assert calls["count"] == 1
