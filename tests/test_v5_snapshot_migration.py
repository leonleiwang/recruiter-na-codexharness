from pathlib import Path

from rn.hiring_requests import HiringRequestRepository


def test_repository_invalidates_in_progress_v4_score_snapshot_on_v5_open(tmp_path: Path):
    db = tmp_path / "requests.sqlite3"
    repo = HiringRequestRepository(db)
    req = repo.create("dingtalk:alice", {"position_title": "检验技术员"}, owner_conversation_id="c1")
    repo.update_for_owner("dingtalk:alice", req["request_id"], jd={"title": "检验技术员"}, status="JD_READY")
    repo.set_ranking_for_owner(
        "dingtalk:alice",
        req["request_id"],
        [{"candidate_id": "C1", "name": "旧候选人", "cs": 91, "jm": 90, "rq": 92}],
        resume_count=10,
        trace_dir="workspace/runs/v4-old",
    )
    repo.next_batch("dingtalk:alice", req["request_id"], 3)

    migrated_repo = HiringRequestRepository(db)
    current = migrated_repo.get_for_owner("dingtalk:alice", req["request_id"])

    assert current["status"] == "JD_READY"
    assert current["ranking"] == []
    assert current["recommended_candidate_ids"] == []
    assert current["recommendation_cursor"] == 0
    assert current["trace_dir"] == ""
    events = migrated_repo.events_for_owner("dingtalk:alice", req["request_id"])
    assert events[-1]["event_type"] == "V5_LEGACY_RECOMMENDATION_INVALIDATED"


def test_repository_preserves_selected_history_during_v5_migration(tmp_path: Path):
    db = tmp_path / "requests.sqlite3"
    repo = HiringRequestRepository(db)
    req = repo.create("dingtalk:alice", {"position_title": "检验技术员"})
    repo.set_ranking_for_owner(
        "dingtalk:alice",
        req["request_id"],
        [{"candidate_id": "C1", "name": "历史已选", "cs": 88}],
        resume_count=5,
    )
    repo.next_batch("dingtalk:alice", req["request_id"], 3)
    repo.select_candidate("dingtalk:alice", req["request_id"], "C1")

    migrated_repo = HiringRequestRepository(db)
    current = migrated_repo.get_for_owner("dingtalk:alice", req["request_id"])
    assert current["status"] == "SELECTED"
    assert current["selected_candidate_id"] == "C1"


def test_repository_keeps_v5_recommendation_snapshot_on_reopen(tmp_path: Path):
    db = tmp_path / "requests.sqlite3"
    repo = HiringRequestRepository(db)
    req = repo.create("dingtalk:alice", {"position_title": "检验技术员"})
    repo.set_ranking_for_owner(
        "dingtalk:alice",
        req["request_id"],
        [{"recommendation_order": 1, "candidate_id": "C1", "name": "新候选人", "decision": "pass", "readiness": "ready"}],
        resume_count=5,
    )

    reopened = HiringRequestRepository(db)
    current = reopened.get_for_owner("dingtalk:alice", req["request_id"])
    assert current["status"] == "CANDIDATES_READY"
    assert current["ranking"][0]["recommendation_order"] == 1
