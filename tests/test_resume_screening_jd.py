"""JD 生成层测试：需求字段 + 画像 + 模板 → 结构化 JD，及模板库检索与 CLI。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from rn.repositories import ProfileRepository
from rn.resume_screening import cli
from rn.resume_screening.jd.generator import (
    build_request_jd,
    render_jd_document,
    render_jd_summary,
    requirement_groups,
)
from rn.resume_screening.jd.templates import JDRepository

FIELDS = {
    "position_title": "检验技术员",
    "department": "检验中心",
    "headcount": 2,
    "location": "杭州市西湖区",
    "salary_range": "8-12K",
    "urgency": "一个月内",
    "employment_type": "社招",
    "core_responsibilities": ["临检标本检测", "室内质控与结果审核"],
    "key_experience_skills": ["PCR", "生化分析仪"],
    "hard_requirements": ["持有检验士资格证"],
    "extra_hard_requirements": ["接受体检季加班"],
    "bonus_points": ["有三甲医院经验优先"],
}

PROFILE_RAW = {
    "profile_id": "PROFILE-TEST",
    "name": "检验技术员",
    "description": "负责检验科临检标本检测与质量控制。",
    "key_abilities": [{"name": "规范操作与执行能力"}],
    "key_experiences": [{"name": "检验科经验"}, {"name": "PCR"}],
}


class _Related:
    jd_id = "JD-IVD-研发类-001"
    title = "IVD试剂研发工程师"


# ---------------------------------------------------------------------------
# 生成器
# ---------------------------------------------------------------------------


def test_build_request_jd_from_fields_only():
    jd = build_request_jd("REQ-1", FIELDS)
    assert jd["schema_version"] == "4.0"
    assert jd["jd_id"] == "JD-REQ-1"
    assert jd["title"] == "检验技术员"
    assert jd["location"] == "杭州市西湖区"
    assert jd["profile_inherited"] is False
    assert jd["related_template"]["match_type"] == "none"
    gates = {item["name"]: item["gate_type"] for item in jd["hard_requirements"]}
    assert gates["工作地点"] == "VERIFY_ONLY"
    assert gates["持有检验士资格证"] == "HARD_GATE"
    assert gates["接受体检季加班"] == "HARD_GATE"
    # 模板内容不得混进这次招聘的专属要求
    assert jd["related_template"]["usage"] == "wording_reference_only"


def test_build_request_jd_inherits_profile_but_not_profile_facts():
    jd = build_request_jd("REQ-2", FIELDS, PROFILE_RAW, _Related())
    assert jd["profile_inherited"] is True
    assert jd["profile_id"] == "PROFILE-TEST"
    # 用户能力 + 画像能力/经验去重合并（画像部分只用于派生匹配标签）
    assert jd["key_experience_skills"] == [
        "PCR",
        "生化分析仪",
        "规范操作与执行能力",
        "检验科经验",
    ]
    assert jd["request_key_experience_skills"] == ["PCR", "生化分析仪"]
    assert requirement_groups(jd)["技能"] == ["PCR", "生化分析仪"]
    # 画像描述只在用户没写职责时兜底
    assert jd["responsibilities"] == ["临检标本检测", "室内质控与结果审核"]
    assert jd["related_template"] == {
        "jd_id": "JD-IVD-研发类-001",
        "title": "IVD试剂研发工程师",
        "match_type": "related",
        "usage": "wording_reference_only",
    }


def test_profile_description_goes_to_context_not_duties():
    """画像描述说明工作场景，不得冒充本次招聘的岗位职责。"""
    fields = dict(FIELDS)
    fields["core_responsibilities"] = []
    jd = build_request_jd("REQ-3", fields, PROFILE_RAW)
    assert jd["responsibilities"] == []
    assert jd["profile_context"] == "负责检验科临检标本检测与质量控制。"


def test_render_jd_summary_mentions_key_parts():
    markdown = render_jd_summary(build_request_jd("REQ-4", FIELDS, PROFILE_RAW, _Related()))
    assert "JD 已整理完成" in markdown
    assert "检验技术员" in markdown
    assert "杭州市西湖区" in markdown
    assert "已使用公司标准岗位画像补全" in markdown
    assert "核心职责：临检标本检测" in markdown
    assert "额外硬要求：" in markdown
    assert "完整 JD" in markdown
    assert "开始筛选" in markdown


# ---------------------------------------------------------------------------
# 发布版 JD 文档（岗位职责 + 任职要求）
# ---------------------------------------------------------------------------


def test_render_jd_document_has_duties_and_grouped_requirements():
    fields = dict(FIELDS)
    fields["hard_requirements"] = [
        "大专及以上学历，医学检验/医学检验技术/临床医学/生物技术等相关专业",
        "持有检验士/检验师资格证（应届可接受已报名待考）",
        "须有医院检验科或第三方医学检验实验室的实习/见习经历",
        "年龄 25-35 岁",
    ]
    doc = render_jd_document(build_request_jd("REQ-5", fields, PROFILE_RAW))
    assert doc.startswith("# 检验技术员")
    assert "- 部门：检验中心" in doc
    assert "- 工作地点：杭州市西湖区" in doc
    assert "- 招聘人数：2 人" in doc
    assert "## 岗位职责" in doc
    assert "1. 临检标本检测" in doc
    assert "2. 室内质控与结果审核" in doc
    assert "## 任职要求" in doc
    assert "**学历**：大专及以上" in doc
    assert "**专业**：医学检验/医学检验技术/临床医学/生物技术等相关专业" in doc
    assert "**证书**：持有检验士/检验师资格证（应届可接受已报名待考）" in doc
    assert "**经验**：须有医院检验科或第三方医学检验实验室的实习/见习经历" in doc
    assert "**其他硬性条件**：年龄 25-35 岁" in doc
    assert "**技能与能力**" in doc
    assert "匹配标签" not in doc
    assert "  - PCR" in doc
    # 画像继承的能力/经验是标签来源，不是 JD 任职要求
    assert "规范操作与执行能力" not in doc
    assert "检验科经验" not in doc
    assert "## 加分项" in doc
    assert "有三甲医院经验优先" in doc
    # 画像描述只出现在「岗位定位」，不混进职责
    assert "## 岗位定位" in doc
    assert doc.index("## 岗位职责") < doc.index("## 岗位定位")
    assert doc.index("## 岗位定位") < doc.index("## 任职要求")


def test_requirement_groups_classify_labeled_and_free_text():
    jd = build_request_jd("REQ-8", {
        "position_title": "检验技术员",
        "hard_requirements": [
            "学历：本科及以上",
            "证书：持有检验师资格证",
            "接受体检季加班",
        ],
        "key_experience_skills": ["PCR"],
    })
    groups = requirement_groups(jd)
    assert groups["学历"] == ["本科及以上"]
    assert groups["证书"] == ["持有检验师资格证"]
    assert groups["其他"] == ["接受体检季加班"]
    assert groups["技能"] == ["PCR"]
    assert groups["专业"] == []


class _RelatedWithDuties:
    jd_id = "JD-ICL-实验室类-018"
    title = "检验技师"
    core_responsibilities = "1. 按照SOP完成临床检验标本的检测工作；\n2. 执行室内质控，确保检测结果准确可靠。"
    requirements_text = "学历：大专及以上"


def test_render_jd_document_uses_template_duties_when_missing():
    fields = dict(FIELDS)
    fields["core_responsibilities"] = []
    jd = build_request_jd("REQ-6", fields, None, _RelatedWithDuties())
    assert jd["responsibilities"] == []
    doc = render_jd_document(jd)
    assert "参考公司同岗位 JD 模板（检验技师 JD-ICL-实验室类-018）" in doc
    assert "1. 按照SOP完成临床检验标本的检测工作" in doc
    assert "2. 执行室内质控，确保检测结果准确可靠" in doc
    assert "1. 1." not in doc


def test_render_jd_document_asks_for_duties_when_nothing_is_available():
    fields = dict(FIELDS)
    fields["core_responsibilities"] = []
    doc = render_jd_document(build_request_jd("REQ-7", fields))
    assert "请用人部门补充 3–5 条" in doc


# ---------------------------------------------------------------------------
# JD 模板库
# ---------------------------------------------------------------------------


def test_default_jd_library_is_verified_and_searchable():
    repo = JDRepository()
    health = repo.health()
    assert health.status == "ok"
    assert repo.count() == 45
    assert repo.search_exact("医学联络官") == []
    exact = repo.search_exact("医学信息沟通专员")
    assert len(exact) == 1
    assert exact[0].title == "医学信息沟通专员"
    related = repo.search_related("医学联络官", limit=5)
    assert related
    assert all(item.title != "医学联络官" for item in related)
    assert all(repo.get(item.jd_id) is item for item in related)


def test_related_jd_prefers_same_domain_prefix():
    related = JDRepository().search_related("检验技术员", limit=5)
    assert related
    assert related[0].title == "检验技师"


def _write_library(path: Path, entries: list[dict], total: int | None = None) -> Path:
    doc = {"schema_version": "3.4", "total": len(entries) if total is None else total, "entries": entries}
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


def _entry(jd_id: str, title: str, status: str = "VERIFIED_SOURCE") -> dict:
    return {
        "jd_id": jd_id,
        "title": title,
        "company_type": "上游-IVD厂家",
        "sub_category": "检验类",
        "core_responsibilities": "检验科日常标本检测",
        "requirements_text": "医学检验技术专业，持有检验士资格证",
        "source_status": status,
        "source_file": "jd.xlsx",
        "source_sheet": "Sheet1",
        "source_row": 2,
    }


def test_custom_library_health_get_list(tmp_path: Path):
    path = _write_library(tmp_path / "jd.json", [_entry("JD-A", "检验技术员"), _entry("JD-B", "PCR工程师")])
    repo = JDRepository(path)
    assert repo.count() == 2
    assert repo.health().status == "ok"
    assert [item.jd_id for item in repo.list_all()] == ["JD-A", "JD-B"]
    assert repo.get("JD-B").title == "PCR工程师"
    assert repo.get("JD-MISSING") is None


def test_unverified_library_entry_is_rejected(tmp_path: Path):
    path = _write_library(tmp_path / "jd.json", [_entry("JD-BAD", "未核验岗位", status="DRAFT")])
    with pytest.raises(ValueError, match="UNVERIFIED_JD_IN_PRODUCTION_LIBRARY"):
        JDRepository(path).count()


def test_library_total_mismatch_reports_degraded(tmp_path: Path):
    path = _write_library(tmp_path / "jd.json", [_entry("JD-A", "检验技术员")], total=9)
    health = JDRepository(path).health()
    assert health.status == "degraded"
    assert "total=9" in (health.error or "")


# ---------------------------------------------------------------------------
# CLI：jd 子命令
# ---------------------------------------------------------------------------


def test_cli_generates_jd_from_fields_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    fields_path = tmp_path / "fields.json"
    fields_path.write_text(
        json.dumps({"request_id": "REQ-CLI", "fields": FIELDS}, ensure_ascii=False), encoding="utf-8"
    )
    rc = cli.main(["jd", "--fields-file", str(fields_path), "--json", "--no-write"])
    assert rc == cli.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["jd_id"] == "JD-REQ-CLI"
    assert payload["title"] == "检验技术员"


def test_cli_inherits_profile_by_id(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    profile = ProfileRepository().get("PROFILE-412167F2937C")
    assert profile is not None
    fields_path = tmp_path / "fields.json"
    fields_path.write_text(json.dumps(FIELDS, ensure_ascii=False), encoding="utf-8")
    rc = cli.main(
        ["jd", "--fields-file", str(fields_path), "--profile-id", "PROFILE-412167F2937C", "--json", "--no-write"]
    )
    assert rc == cli.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["profile_inherited"] is True
    assert payload["profile_id"] == "PROFILE-412167F2937C"
    assert payload["responsibilities"] == ["临检标本检测", "室内质控与结果审核"]


def test_cli_uses_related_template_for_wording(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    fields_path = tmp_path / "fields.json"
    fields_path.write_text(json.dumps(FIELDS, ensure_ascii=False), encoding="utf-8")
    rc = cli.main(
        ["jd", "--fields-file", str(fields_path), "--related-jd-title", "医学联络官", "--json", "--no-write"]
    )
    assert rc == cli.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["related_template"]["match_type"] == "related"
    assert payload["related_template"]["usage"] == "wording_reference_only"


def test_cli_unknown_profile_is_input_error(tmp_path: Path):
    fields_path = tmp_path / "fields.json"
    fields_path.write_text(json.dumps(FIELDS, ensure_ascii=False), encoding="utf-8")
    rc = cli.main(["jd", "--fields-file", str(fields_path), "--profile-id", "PROFILE-NOPE", "--no-write"])
    assert rc == cli.EXIT_INPUT_ERROR


def test_cli_bad_fields_json_is_input_error(tmp_path: Path):
    fields_path = tmp_path / "fields.json"
    fields_path.write_text("{not json", encoding="utf-8")
    rc = cli.main(["jd", "--fields-file", str(fields_path), "--no-write"])
    assert rc == cli.EXIT_INPUT_ERROR


def test_cli_writes_jd_and_refuses_overwrite(tmp_path: Path):
    fields_path = tmp_path / "fields.json"
    fields_path.write_text(json.dumps(FIELDS, ensure_ascii=False), encoding="utf-8")
    out_dir = tmp_path / "out"
    rc = cli.main(["jd", "--fields-file", str(fields_path), "--out", str(out_dir)])
    assert rc == cli.EXIT_OK
    assert (out_dir / "jd.json").exists()
    md = (out_dir / "jd.md").read_text(encoding="utf-8")
    assert md.startswith("# 检验技术员")
    assert "## 岗位职责" in md
    assert "## 任职要求" in md
    assert "JD 已整理完成" not in md

    rc_again = cli.main(["jd", "--fields-file", str(fields_path), "--out", str(out_dir)])
    assert rc_again == cli.EXIT_ANALYSIS_ERROR

    rc_overwrite = cli.main(["jd", "--fields-file", str(fields_path), "--out", str(out_dir), "--overwrite"])
    assert rc_overwrite == cli.EXIT_OK


def test_cli_records_department_confirmation(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """JD 发布前的用人部门确认要留痕：jd.confirmation.json + 自检报告里的确认行。"""
    fields_path = tmp_path / "fields.json"
    fields_path.write_text(json.dumps(FIELDS, ensure_ascii=False), encoding="utf-8")

    pending_dir = tmp_path / "pending"
    rc = cli.main(["jd", "--fields-file", str(fields_path), "--check", "--out", str(pending_dir)])
    # FIELDS 的职责只有 2 条，自检会报 ERROR；这里只验证确认留痕，不要求自检通过。
    assert rc in (cli.EXIT_OK, cli.EXIT_QUALITY_ERROR)
    pending_quality = (pending_dir / "jd.quality.md").read_text(encoding="utf-8")
    assert "发布确认：待用人部门确认" in pending_quality
    assert not (pending_dir / "jd.confirmation.json").exists()

    confirmed_dir = tmp_path / "confirmed"
    rc = cli.main([
        "jd", "--fields-file", str(fields_path), "--check",
        "--confirm-by", "王主管", "--confirm-note", "职责第 3 条已与科室核对",
        "--out", str(confirmed_dir),
    ])
    assert rc in (cli.EXIT_OK, cli.EXIT_QUALITY_ERROR)
    confirmation = json.loads((confirmed_dir / "jd.confirmation.json").read_text(encoding="utf-8"))
    assert confirmation["confirmed_by"] == "王主管"
    assert "职责第 3 条已与科室核对" in confirmation["note"]
    assert confirmation["jd_id"]
    confirmed_quality = (confirmed_dir / "jd.quality.md").read_text(encoding="utf-8")
    assert "发布确认：已确认（王主管" in confirmed_quality
