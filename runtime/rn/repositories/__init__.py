"""Recruiter-Na 数据仓库门面（画像 / 简历 / JD 模板）。

- ProfileRepository: 从源 PDF 重建并校验的标准岗位画像。
- JDRepository: 从源 Excel 重建并校验的 JD 模板库。
- ResumeRepository: 生产 MySQL；JSON 只是开发夹具。

`ResumeRepository` 的实现已随简历解析功能迁到
`rn.resume_screening.parsing.resume_repository`；`JDRepository` 已随 JD 生成功能
迁到 `rn.resume_screening.jd.templates`。这里保留同名再导出以维持
``from rn.repositories import ...`` 这一既有用法；因为子项目模块自身需要
`rn.repositories.models`，为避免循环导入，再导出通过模块级 ``__getattr__``
惰性完成。
"""
from rn.repositories.models import DataSourceHealth, DataSourcesSnapshot, JDRecord, ProfileRecord, ResumeRecord
from rn.repositories.profile_repository import ProfileRepository

__all__ = [
    "ProfileRepository", "JDRepository", "ResumeRepository",
    "ProfileRecord", "JDRecord", "ResumeRecord", "DataSourceHealth", "DataSourcesSnapshot",
]


def __getattr__(name: str):
    if name == "ResumeRepository":
        from rn.resume_screening.parsing.resume_repository import ResumeRepository

        return ResumeRepository
    if name == "JDRepository":
        from rn.resume_screening.jd.templates import JDRepository

        return JDRepository
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
