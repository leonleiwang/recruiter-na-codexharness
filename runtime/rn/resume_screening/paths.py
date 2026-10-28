"""Shared path constants for the resume-screening package.

Git 仓库根目录按本文件位置推导：``runtime/rn/resume_screening/paths.py``
→ ``parents[0]`` = resume_screening，``parents[1]`` = rn，``parents[2]`` = runtime，
``parents[3]`` = 仓库根。包内其它模块一律从这里取根目录，避免各自数层级。
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNTIME_ROOT = REPO_ROOT / "runtime"
KNOWLEDGE_ROOT = REPO_ROOT / "knowledge"
POLICY_ROOT = KNOWLEDGE_ROOT / "recruiting" / "policies"
DEFAULT_POLICY_PATH = POLICY_ROOT / "inspection_technician_v1.json"
WORKSPACE_ROOT = REPO_ROOT / "workspace"
ANALYSIS_RUN_ROOT = WORKSPACE_ROOT / "runs" / "resume-screening-analysis"
JD_RUN_ROOT = WORKSPACE_ROOT / "runs" / "resume-screening-jd"

__all__ = [
    "REPO_ROOT",
    "RUNTIME_ROOT",
    "KNOWLEDGE_ROOT",
    "POLICY_ROOT",
    "DEFAULT_POLICY_PATH",
    "WORKSPACE_ROOT",
    "ANALYSIS_RUN_ROOT",
    "JD_RUN_ROOT",
]
