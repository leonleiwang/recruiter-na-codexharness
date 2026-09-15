"""Best-effort business progress events for CodexHarness IM adapters.

The screening pipeline writes one JSON object per line to the path provided by
``CODEXHARNESS_PROGRESS_FILE``.  The IM runtime owns that file and tails it while
Codex is executing.  Progress reporting is observability/UI only: a write
failure must never change a candidate's score or screening result.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def emit_progress(
    stage: str,
    text: str,
    *,
    current: int | None = None,
    total: int | None = None,
    status: str = "running",
    **extra: Any,
) -> None:
    target = str(os.getenv("CODEXHARNESS_PROGRESS_FILE") or "").strip()
    if not target:
        return
    row: dict[str, Any] = {
        "stage": str(stage or "UNKNOWN"),
        "text": str(text or ""),
        "status": str(status or "running"),
        "timestamp_ms": int(time.time() * 1000),
    }
    if current is not None:
        row["current"] = int(current)
    if total is not None:
        row["total"] = int(total)
    row.update({k: v for k, v in extra.items() if v is not None})
    try:
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
    except Exception:
        # Progress is UX/telemetry only. Never break the screening pipeline.
        return
