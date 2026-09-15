from pathlib import Path

import pytest

from rn.hiring_requests import HiringRequestRepository


def test_owner_scoped_requests_do_not_leak(tmp_path: Path):
    repo = HiringRequestRepository(tmp_path / "requests.sqlite3")
    a = repo.create("dingtalk:alice", {"position_title": "医学联络官"})
    b = repo.create("dingtalk:bob", {"position_title": "病理医生"})

    assert repo.get_for_owner("dingtalk:alice", a["request_id"])["position_title"] == "医学联络官"
    assert repo.get_for_owner("dingtalk:alice", b["request_id"]) is None
    assert [x["request_id"] for x in repo.list_for_owner("dingtalk:alice")] == [a["request_id"]]


def test_next_batch_never_repeats_candidates_and_exhausts(tmp_path: Path):
    repo = HiringRequestRepository(tmp_path / "requests.sqlite3")
    req = repo.create("dingtalk:alice", {"position_title": "医学联络官"})
    ranking = [
        {"candidate_id": f"C{i}", "name": f"候选人{i}", "cs": 100 - i}
        for i in range(1, 8)
    ]
    repo.set_ranking_for_owner("dingtalk:alice", req["request_id"], ranking, resume_count=146)

    first = repo.next_batch("dingtalk:alice", req["request_id"], 3)
    second = repo.next_batch("dingtalk:alice", req["request_id"], 3)
    third = repo.next_batch("dingtalk:alice", req["request_id"], 3)
    fourth = repo.next_batch("dingtalk:alice", req["request_id"], 3)

    assert [x["candidate_id"] for x in first] == ["C1", "C2", "C3"]
    assert [x["candidate_id"] for x in second] == ["C4", "C5", "C6"]
    assert [x["candidate_id"] for x in third] == ["C7"]
    assert fourth == []
    request = repo.get_for_owner("dingtalk:alice", req["request_id"])
    assert request["recommendation_cursor"] == 7
    assert request["recommended_candidate_ids"] == [f"C{i}" for i in range(1, 8)]


def test_selection_is_owner_scoped(tmp_path: Path):
    repo = HiringRequestRepository(tmp_path / "requests.sqlite3")
    req = repo.create("dingtalk:alice", {"position_title": "医学联络官"})
    repo.set_ranking_for_owner("dingtalk:alice", req["request_id"], [{"candidate_id": "C1", "name": "A", "cs": 80}], resume_count=10)
    repo.next_batch("dingtalk:alice", req["request_id"], 3)

    with pytest.raises(PermissionError):
        repo.select_candidate("dingtalk:bob", req["request_id"], "C1")

    selected = repo.select_candidate("dingtalk:alice", req["request_id"], "C1")
    assert selected["status"] == "SELECTED"
    assert selected["selected_candidate_id"] == "C1"

def test_reranking_preserves_lifetime_seen_candidates_and_never_repeats(tmp_path: Path):
    repo = HiringRequestRepository(tmp_path / "requests.sqlite3")
    req = repo.create("dingtalk:alice", {"position_title": "医学联络官"})
    repo.set_ranking_for_owner("dingtalk:alice", req["request_id"], [
        {"candidate_id": "C1", "name": "一"}, {"candidate_id": "C2", "name": "二"}, {"candidate_id": "C3", "name": "三"},
    ], resume_count=10)
    assert [x["candidate_id"] for x in repo.next_batch("dingtalk:alice", req["request_id"], 3)] == ["C1", "C2", "C3"]

    # A later sourcing pass can contain old candidates again; they must remain suppressed.
    repo.set_ranking_for_owner("dingtalk:alice", req["request_id"], [
        {"candidate_id": "C1", "name": "一"}, {"candidate_id": "C4", "name": "四"}, {"candidate_id": "C2", "name": "二"},
    ], resume_count=12)
    assert [x["candidate_id"] for x in repo.next_batch("dingtalk:alice", req["request_id"], 3)] == ["C4"]
    current = repo.get_for_owner("dingtalk:alice", req["request_id"])
    assert current["recommended_candidate_ids"] == ["C1", "C2", "C3", "C4"]
