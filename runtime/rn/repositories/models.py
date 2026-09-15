"""Shared data models for the three independent knowledge repositories.

Recruiter-Na v3.4.0: profile / JD / resume are three separate data sources
with their own schemas, contracts, and lifecycle. They MUST NOT be merged
into a single "combined" store.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProfileRecord:
    """A single standard position profile (标准岗位画像)."""
    profile_id: str
    name: str
    aliases: list[str] = field(default_factory=list)
    category: str = ""
    department: str = ""
    capabilities: list[dict[str, Any]] = field(default_factory=list)
    requirements: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class JDRecord:
    """A single JD template (标准岗位JD)."""
    jd_id: str
    title: str
    company_type: str = ""
    sub_category: str = ""
    core_responsibilities: str = ""
    requirements_text: str = ""
    source_file: str = ""
    source_sheet: str = ""
    source_row: int = 0


@dataclass
class ResumeRecord:
    """A single candidate resume record (from MySQL or local JSON)."""
    resume_id: str
    name: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class DataSourceHealth:
    """Health check result for a single data source."""
    status: str  # "ok" | "unavailable" | "degraded"
    backend: str = ""
    count: int | None = None
    error: str | None = None


@dataclass
class DataSourcesSnapshot:
    """Full health snapshot of all three data sources for a screening run."""
    profile_library: DataSourceHealth = field(default_factory=lambda: DataSourceHealth(status="unknown"))
    jd_library: DataSourceHealth = field(default_factory=lambda: DataSourceHealth(status="unknown"))
    resume_library: DataSourceHealth = field(default_factory=lambda: DataSourceHealth(status="unknown"))