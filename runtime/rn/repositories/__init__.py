"""Three independent Recruiter-Na repositories.

- ProfileRepository: verified standard profiles generated from source PDFs.
- JDRepository: verified JD templates generated from the source workbook.
- ResumeRepository: production MySQL; JSON is development-fixture only.
"""
from rn.repositories.models import DataSourceHealth, DataSourcesSnapshot, JDRecord, ProfileRecord, ResumeRecord
from rn.repositories.profile_repository import ProfileRepository
from rn.repositories.jd_repository import JDRepository
from rn.repositories.resume_repository import ResumeRepository

__all__ = [
    "ProfileRepository", "JDRepository", "ResumeRepository",
    "ProfileRecord", "JDRecord", "ResumeRecord", "DataSourceHealth", "DataSourcesSnapshot",
]
