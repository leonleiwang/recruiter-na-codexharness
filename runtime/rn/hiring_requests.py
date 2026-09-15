"""Owner-scoped hiring-request persistence for Recruiter-Na v5.

The store intentionally keeps hiring-manager workflow state separate from the
resume database. Every public read/write requires ``owner_staff_id`` so a
hiring manager cannot enumerate another person's requests by guessing IDs.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "workspace" / "hiring_manager_requests.sqlite3"


STATUSES = {
    "DRAFT",
    "JD_READY",
    "SCREENING",
    "CANDIDATES_READY",
    "REVIEWING_CANDIDATES",
    "WAITING_HR_SOURCING",
    "NEW_CANDIDATES_AVAILABLE",
    "SELECTED",
    "PAUSED",
    "CANCELLED",
    "CLOSED",
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _j(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


class HiringRequestRepository:
    def __init__(self, db_path: str | Path | None = None):
        configured = db_path or os.environ.get("RECRUITER_REQUEST_DB") or DEFAULT_DB
        self.path = Path(configured)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        self._migrate_v5_legacy_recommendation_snapshots()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS recruitment_requests (
                    request_id TEXT PRIMARY KEY,
                    owner_staff_id TEXT NOT NULL,
                    owner_conversation_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    position_title TEXT NOT NULL DEFAULT '',
                    profile_match_status TEXT NOT NULL DEFAULT 'UNASSESSED',
                    profile_id TEXT NOT NULL DEFAULT '',
                    profile_confidence REAL,
                    profile_reason TEXT NOT NULL DEFAULT '',
                    request_json TEXT NOT NULL DEFAULT '{}',
                    jd_json TEXT NOT NULL DEFAULT '{}',
                    ranking_json TEXT NOT NULL DEFAULT '[]',
                    recommendation_cursor INTEGER NOT NULL DEFAULT 0,
                    recommended_candidate_ids_json TEXT NOT NULL DEFAULT '[]',
                    selected_candidate_id TEXT NOT NULL DEFAULT '',
                    last_screen_resume_count INTEGER NOT NULL DEFAULT 0,
                    sourcing_status TEXT NOT NULL DEFAULT '',
                    hr_owner TEXT NOT NULL DEFAULT '',
                    hr_note TEXT NOT NULL DEFAULT '',
                    last_sourced_at TEXT NOT NULL DEFAULT '',
                    new_resume_count INTEGER NOT NULL DEFAULT 0,
                    previous_status TEXT NOT NULL DEFAULT '',
                    trace_dir TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_requests_owner_updated
                    ON recruitment_requests(owner_staff_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS recruitment_request_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    owner_staff_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(request_id) REFERENCES recruitment_requests(request_id)
                );
                CREATE INDEX IF NOT EXISTS idx_request_events_request
                    ON recruitment_request_events(request_id, event_id);
                """
            )

    @staticmethod
    def _is_v5_recommendation_snapshot(value: str | None) -> bool:
        rows = _loads(value, [])
        if not isinstance(rows, list) or not rows:
            return False
        return all(
            isinstance(row, dict)
            and row.get("decision") == "pass"
            and isinstance(row.get("recommendation_order"), int)
            for row in rows
        )

    def _migrate_v5_legacy_recommendation_snapshots(self) -> int:
        """Invalidate only in-progress v4 candidate-score snapshots.

        v5 deliberately keeps the ``ranking_json`` column for schema safety, but
        its payload is a recommendation-order snapshot. A v4 score snapshot may
        otherwise leak into ``换一批`` after upgrade. Completed/selected history
        is not rewritten.
        """
        migrated = 0
        candidate_states = {"CANDIDATES_READY", "REVIEWING_CANDIDATES"}
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT request_id, owner_staff_id, status, previous_status, ranking_json
                   FROM recruitment_requests
                   WHERE ranking_json IS NOT NULL AND ranking_json != '[]'"""
            ).fetchall()
            for row in rows:
                status = str(row["status"] or "")
                previous = str(row["previous_status"] or "")
                active_legacy = status in candidate_states or (status == "PAUSED" and previous in candidate_states)
                if not active_legacy or self._is_v5_recommendation_snapshot(row["ranking_json"]):
                    continue
                conn.execute(
                    """UPDATE recruitment_requests
                       SET status='JD_READY', ranking_json='[]', recommendation_cursor=0,
                           recommended_candidate_ids_json='[]', previous_status='', trace_dir='',
                           sourcing_status='', new_resume_count=0, updated_at=?
                       WHERE request_id=? AND owner_staff_id=?""",
                    (_now(), row["request_id"], row["owner_staff_id"]),
                )
                self._event(
                    conn, row["request_id"], row["owner_staff_id"],
                    "V5_LEGACY_RECOMMENDATION_INVALIDATED",
                    {"from_status": status, "reason": "V4_SCORE_SNAPSHOT_INCOMPATIBLE_WITH_V5_DECISION"},
                )
                migrated += 1
        return migrated

    def _row(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        out = dict(row)
        out["request"] = _loads(out.pop("request_json", None), {})
        out["jd"] = _loads(out.pop("jd_json", None), {})
        out["ranking"] = _loads(out.pop("ranking_json", None), [])
        out["recommended_candidate_ids"] = _loads(out.pop("recommended_candidate_ids_json", None), [])
        # Convenience projection for common fields stored in request JSON.
        for key, value in out["request"].get("fields", {}).items():
            out.setdefault(key, value)
        return out

    def _event(self, conn: sqlite3.Connection, request_id: str, owner: str, event_type: str, payload: Any = None) -> None:
        conn.execute(
            "INSERT INTO recruitment_request_events(request_id, owner_staff_id, event_type, payload_json, created_at) VALUES(?,?,?,?,?)",
            (request_id, owner, event_type, _j(payload or {}), _now()),
        )

    def create(self, owner_staff_id: str, data: dict[str, Any], owner_conversation_id: str = "") -> dict[str, Any]:
        owner = str(owner_staff_id or "").strip()
        if not owner:
            raise ValueError("OWNER_STAFF_ID_REQUIRED")
        request_id = data.get("request_id") or f"REQ-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
        fields = dict(data.get("fields") or {})
        if data.get("position_title") and not fields.get("position_title"):
            fields["position_title"] = data["position_title"]
        request_payload = {
            "fields": fields,
            "provided_fields": list(data.get("provided_fields") or (["position_title"] if fields.get("position_title") else [])),
            "profile_match": dict(data.get("profile_match") or {"status": "UNASSESSED"}),
        }
        for key in ("notes", "source_text"):
            if key in data:
                request_payload[key] = data[key]
        now = _now()
        status = str(data.get("status") or "DRAFT")
        if status not in STATUSES:
            raise ValueError("REQUEST_STATUS_INVALID")
        profile_match = request_payload["profile_match"]
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO recruitment_requests(
                    request_id, owner_staff_id, owner_conversation_id, status, position_title,
                    profile_match_status, profile_id, profile_confidence, profile_reason,
                    request_json, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    request_id, owner, owner_conversation_id, status, fields.get("position_title", ""),
                    profile_match.get("status", "UNASSESSED"), profile_match.get("profile_id", ""),
                    profile_match.get("confidence"), profile_match.get("reason", ""), _j(request_payload), now, now,
                ),
            )
            self._event(conn, request_id, owner, "REQUEST_CREATED", {"status": status})
        return self.get_for_owner(owner, request_id) or {}

    def get_for_owner(self, owner_staff_id: str, request_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM recruitment_requests WHERE request_id=? AND owner_staff_id=?",
                (request_id, owner_staff_id),
            ).fetchone()
        return self._row(row)

    def latest_for_owner(self, owner_staff_id: str, statuses: Iterable[str] | None = None) -> dict[str, Any] | None:
        items = self.list_for_owner(owner_staff_id, statuses=statuses, limit=1)
        return items[0] if items else None

    def list_for_owner(self, owner_staff_id: str, statuses: Iterable[str] | None = None, limit: int = 50) -> list[dict[str, Any]]:
        params: list[Any] = [owner_staff_id]
        sql = "SELECT * FROM recruitment_requests WHERE owner_staff_id=?"
        values = list(statuses or [])
        if values:
            sql += " AND status IN (" + ",".join("?" for _ in values) + ")"
            params.extend(values)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row(r) for r in rows if r is not None]

    def update_for_owner(self, owner_staff_id: str, request_id: str, *, request: dict[str, Any] | None = None,
                         jd: dict[str, Any] | None = None, status: str | None = None,
                         trace_dir: str | None = None, event_type: str = "REQUEST_UPDATED",
                         extra: dict[str, Any] | None = None) -> dict[str, Any]:
        current = self.get_for_owner(owner_staff_id, request_id)
        if current is None:
            raise PermissionError("REQUEST_NOT_FOUND_OR_NOT_OWNED")
        if status is not None and status not in STATUSES:
            raise ValueError("REQUEST_STATUS_INVALID")
        req = request if request is not None else current["request"]
        jd_value = jd if jd is not None else current["jd"]
        profile_match = req.get("profile_match") or {}
        fields = req.get("fields") or {}
        assignments = {
            "request_json": _j(req),
            "jd_json": _j(jd_value),
            "position_title": str(fields.get("position_title") or current.get("position_title") or ""),
            "profile_match_status": str(profile_match.get("status") or current.get("profile_match_status") or "UNASSESSED"),
            "profile_id": str(profile_match.get("profile_id") or current.get("profile_id") or ""),
            "profile_confidence": profile_match.get("confidence"),
            "profile_reason": str(profile_match.get("reason") or ""),
            "status": status or current["status"],
            "trace_dir": trace_dir if trace_dir is not None else current.get("trace_dir", ""),
            "updated_at": _now(),
        }
        if extra:
            allowed = {
                "sourcing_status", "hr_owner", "hr_note", "last_sourced_at", "new_resume_count",
                "previous_status", "selected_candidate_id", "last_screen_resume_count",
            }
            for key, value in extra.items():
                if key in allowed:
                    assignments[key] = value
        sql = "UPDATE recruitment_requests SET " + ", ".join(f"{k}=?" for k in assignments) + " WHERE request_id=? AND owner_staff_id=?"
        with self._connect() as conn:
            conn.execute(sql, [*assignments.values(), request_id, owner_staff_id])
            self._event(conn, request_id, owner_staff_id, event_type, {"status": assignments["status"]})
        return self.get_for_owner(owner_staff_id, request_id) or {}

    def set_recommendations_for_owner(self, owner_staff_id: str, request_id: str, recommendations: list[dict[str, Any]], *, resume_count: int,
                                      trace_dir: str = "") -> dict[str, Any]:
        """Persist a v5 recommendation-order snapshot in the legacy ranking_json column."""
        current = self.get_for_owner(owner_staff_id, request_id)
        if current is None:
            raise PermissionError("REQUEST_NOT_FOUND_OR_NOT_OWNED")
        seen = list(current.get("recommended_candidate_ids") or [])
        with self._connect() as conn:
            conn.execute(
                """UPDATE recruitment_requests SET ranking_json=?, recommendation_cursor=0,
                   recommended_candidate_ids_json=?, last_screen_resume_count=?, status=?, trace_dir=?, updated_at=?
                   WHERE request_id=? AND owner_staff_id=?""",
                (_j(recommendations), _j(seen), int(resume_count), "CANDIDATES_READY", trace_dir, _now(), request_id, owner_staff_id),
            )
            self._event(conn, request_id, owner_staff_id, "RECOMMENDATION_SNAPSHOT_CREATED", {"count": len(recommendations), "resume_count": resume_count})
        return self.get_for_owner(owner_staff_id, request_id) or {}

    def set_ranking_for_owner(self, owner_staff_id: str, request_id: str, ranking: list[dict[str, Any]], *, resume_count: int,
                              trace_dir: str = "") -> dict[str, Any]:
        current = self.get_for_owner(owner_staff_id, request_id)
        if current is None:
            raise PermissionError("REQUEST_NOT_FOUND_OR_NOT_OWNED")
        seen = list(current.get("recommended_candidate_ids") or [])
        with self._connect() as conn:
            conn.execute(
                """UPDATE recruitment_requests SET ranking_json=?, recommendation_cursor=0,
                   recommended_candidate_ids_json=?, last_screen_resume_count=?, status=?, trace_dir=?, updated_at=?
                   WHERE request_id=? AND owner_staff_id=?""",
                (_j(ranking), _j(seen), int(resume_count), "CANDIDATES_READY", trace_dir, _now(), request_id, owner_staff_id),
            )
            self._event(conn, request_id, owner_staff_id, "RANKING_SNAPSHOT_CREATED", {"count": len(ranking), "resume_count": resume_count})
        return self.get_for_owner(owner_staff_id, request_id) or {}

    def next_batch(self, owner_staff_id: str, request_id: str, batch_size: int = 3) -> list[dict[str, Any]]:
        current = self.get_for_owner(owner_staff_id, request_id)
        if current is None:
            raise PermissionError("REQUEST_NOT_FOUND_OR_NOT_OWNED")
        ranking = list(current["ranking"])
        cursor = int(current.get("recommendation_cursor") or 0)
        recommended = list(current["recommended_candidate_ids"])
        seen = set(recommended)
        batch = []
        scan = cursor
        wanted = max(1, int(batch_size))
        while scan < len(ranking) and len(batch) < wanted:
            item = ranking[scan]
            scan += 1
            cid = str(item.get("candidate_id") or "")
            if not cid or cid in seen:
                continue
            batch.append(item)
            seen.add(cid)
            recommended.append(cid)
        new_cursor = scan
        new_status = "REVIEWING_CANDIDATES" if batch else "WAITING_HR_SOURCING"
        with self._connect() as conn:
            conn.execute(
                "UPDATE recruitment_requests SET recommendation_cursor=?, recommended_candidate_ids_json=?, status=?, sourcing_status=?, updated_at=? WHERE request_id=? AND owner_staff_id=?",
                (new_cursor, _j(recommended), new_status, "WAITING_HR_SOURCING" if not batch else "", _now(), request_id, owner_staff_id),
            )
            self._event(conn, request_id, owner_staff_id, "CANDIDATE_BATCH_SHOWN", {"candidate_ids": [x.get("candidate_id") for x in batch]})
        return batch

    def select_candidate(self, owner_staff_id: str, request_id: str, candidate_id: str) -> dict[str, Any]:
        current = self.get_for_owner(owner_staff_id, request_id)
        if current is None:
            raise PermissionError("REQUEST_NOT_FOUND_OR_NOT_OWNED")
        shown = set(current["recommended_candidate_ids"])
        if candidate_id not in shown:
            raise ValueError("CANDIDATE_NOT_RECOMMENDED_TO_OWNER")
        with self._connect() as conn:
            conn.execute(
                "UPDATE recruitment_requests SET selected_candidate_id=?, status='SELECTED', updated_at=? WHERE request_id=? AND owner_staff_id=?",
                (candidate_id, _now(), request_id, owner_staff_id),
            )
            self._event(conn, request_id, owner_staff_id, "CANDIDATE_SELECTED", {"candidate_id": candidate_id})
        return self.get_for_owner(owner_staff_id, request_id) or {}

    def events_for_owner(self, owner_staff_id: str, request_id: str) -> list[dict[str, Any]]:
        if self.get_for_owner(owner_staff_id, request_id) is None:
            raise PermissionError("REQUEST_NOT_FOUND_OR_NOT_OWNED")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT event_type,payload_json,created_at FROM recruitment_request_events WHERE request_id=? AND owner_staff_id=? ORDER BY event_id",
                (request_id, owner_staff_id),
            ).fetchall()
        return [{"event_type": r["event_type"], "payload": _loads(r["payload_json"], {}), "created_at": r["created_at"]} for r in rows]
