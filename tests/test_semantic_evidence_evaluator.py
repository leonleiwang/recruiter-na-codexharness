import json
import pytest

from rn.resume_screening.decision.semantic_evidence_evaluator import (
    EvidenceEvaluationError,
    SemanticEvidenceEvaluator,
    extract_response_text,
)


def test_extract_response_text_supports_responses_api_output_shape():
    payload = {
        "output": [{"content": [{"type": "output_text", "text": "{\"ok\":true}"}]}]
    }
    assert extract_response_text(payload) == '{"ok":true}'


def test_evaluator_parses_json_and_requires_complete_contract():
    captured = {}
    def post_json(url, headers, payload, timeout):
        captured.update({"url": url, "headers": headers, "payload": payload, "timeout": timeout})
        doc = {
            "schema_version": "3.0", "candidate_id": "C1", "job_id": "J1",
            "evaluation_status": "COMPLETE",
            "tag_assessments": [], "requirement_assessments": [],
        }
        return {"output_text": json.dumps(doc, ensure_ascii=False)}

    evaluator = SemanticEvidenceEvaluator(
        base_url="https://llm.example/v1", model="semantic-model", api_key="secret",
        post_json=post_json, timeout_seconds=17,
    )
    out = evaluator.evaluate({"schema_version": "3.0", "job_id": "J1", "tags": [], "requirements": []},
                             {"candidate_id": "C1", "raw_text": "真实简历"})
    assert out["evaluation_status"] == "COMPLETE"
    assert captured["url"].endswith("/responses")
    assert captured["headers"]["Authorization"] == "Bearer secret"
    prompt = captured["payload"]["input"]
    assert "关键词" in prompt and "不得" in prompt
    assert "逐字" in prompt or "原文" in prompt


def test_transport_failure_raises_and_never_returns_keyword_fallback():
    def fail(*_args, **_kwargs):
        raise TimeoutError("timeout")
    evaluator = SemanticEvidenceEvaluator(
        base_url="https://llm.example/v1", model="m", api_key="k", post_json=fail,
    )
    with pytest.raises(EvidenceEvaluationError) as exc:
        evaluator.evaluate({"schema_version": "3.0", "job_id": "J1", "tags": [], "requirements": []},
                           {"candidate_id": "C1", "raw_text": "Kubernetes Kubernetes Kubernetes"})
    assert exc.value.code == "LLM_REQUEST_FAILED"


def test_retry_transport_retries_retryable_errors_then_succeeds():
    from rn.resume_screening.decision.semantic_evidence_evaluator import post_json_with_retry
    attempts = []
    sleeps = []
    def once(url, headers, payload, timeout):
        attempts.append(1)
        if len(attempts) < 3:
            raise EvidenceEvaluationError("LLM_HTTP_RETRYABLE", "HTTP 429")
        return {"output_text": "ok"}
    out = post_json_with_retry(
        once, "https://x/responses", {}, {}, 5,
        max_attempts=5, sleep_fn=lambda seconds: sleeps.append(seconds),
    )
    assert out["output_text"] == "ok"
    assert len(attempts) == 3
    assert sleeps == [1, 2]


def test_retry_transport_does_not_retry_non_retryable_contract_error():
    from rn.resume_screening.decision.semantic_evidence_evaluator import post_json_with_retry
    attempts = []
    def once(*_args):
        attempts.append(1)
        raise EvidenceEvaluationError("LLM_HTTP_ERROR", "HTTP 400")
    with pytest.raises(EvidenceEvaluationError):
        post_json_with_retry(once, "https://x", {}, {}, 5, sleep_fn=lambda _s: None)
    assert len(attempts) == 1
