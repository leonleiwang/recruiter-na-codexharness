"""对外发布 JD 撰写规范、自检、对外版渲染与 LLM 起草的测试。

规范来源：成熟招聘工具的 JD 生成流程（三模块 / 职责强动词 15–30 字 5–7 条 /
任职要求按 KSAO 5–7 条 / 全文 400–600 字 / 无企业信息与敏感词）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from rn.resume_screening import cli
from rn.resume_screening.analysis.requirements import (
    render_requirements_markdown,
    requirements_from_jd,
)
from rn.resume_screening.jd.draft import (
    JdDraftUnavailable,
    build_draft_prompt,
    draft_request_jd,
)
from rn.resume_screening.jd.generator import (
    build_request_jd,
    public_location,
    render_jd_document,
    sanitize_title,
)
from rn.resume_screening.jd.intake import HiringAnalysisError
from rn.resume_screening.jd.presentation import render_presentation
from rn.resume_screening.jd.quality import (
    document_char_count,
    is_strong_verb_start,
    ksao_groups,
    render_quality_report,
    validate_jd,
)
from rn.resume_screening.jd.templates import is_related_title

DUTIES = [
    "负责临床检验样本的接收、编号、离心、分装与保存管理。",
    "执行血常规、尿常规、生化、免疫等常规项目的检测与复核。",
    "负责血球仪、生化仪、化学发光仪等检验仪器的日常操作与校准。",
    "执行室内质控与室间质评，记录并复核异常结果，及时上报。",
    "负责检验报告的录入、审核、发放与归档管理，保证结果可追溯。",
    "执行生物安全规范，做好标本接收区与医疗废弃物的消毒处置。",
]

REQUIREMENTS = [
    "学历：大专及以上学历，医学检验技术、临床医学等相关专业",
    "专业：医学检验、医学检验技术、临床医学、生物技术等专业",
    "经验：有医院检验科或第三方医学检验实验室的实习、见习经历",
    "证书：持有检验士或检验师资格证，应届生可接受已报名待考",
    "技能：熟练操作血球仪、生化仪、化学发光仪等常用检验设备",
    "职业素养：责任心强、细致严谨，具备良好的沟通表达与团队协作能力",
]

COMPLIANT_FIELDS: dict[str, Any] = {
    "position_title": "医学检验技术员",
    "department": "临床检测中心",
    "headcount": 2,
    "location": "杭州集团总部实验室",
    "salary_range": "3.5k-4k/月",
    "urgency": "紧急",
    "core_responsibilities": DUTIES,
    "hard_requirements": REQUIREMENTS,
    "bonus_points": ["有分子生物学或微生物检测经验者优先"],
}

SPARSE_FIELDS: dict[str, Any] = {
    "position_title": "检验技术员",
    "location": "杭州集团总部实验室",
    "core_responsibilities": ["负责临床检验标本的检测。"],
    "hard_requirements": ["学历：大专及以上"],
}


def _compliant_jd() -> dict[str, Any]:
    return build_request_jd("REQ-QUALITY", COMPLIANT_FIELDS)


class _FakeAnalyzer:
    """替身：只实现 ``_call``，避免测试依赖真实 LLM。"""

    def __init__(self, payload: dict[str, Any] | None = None, error: Exception | None = None):
        self.payload = payload
        self.error = error
        self.prompts: list[str] = []

    def _call(self, prompt: str) -> dict[str, Any]:
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return self.payload or {}


# ---------------------------------------------------------------------------
# 规范基础件
# ---------------------------------------------------------------------------


def test_strong_verb_detection():
    assert is_strong_verb_start("负责临床检验样本的接收与处理。") == "负责"
    assert is_strong_verb_start("执行室内质控与结果复核。") == "执行"
    assert is_strong_verb_start("按照SOP完成临床检验标本的检测工作") == ""
    assert is_strong_verb_start("在组长安排下完成检测") == ""


def test_document_char_count_ignores_markdown():
    document = "# 标题\n\n## 岗位职责\n1. **负责**样本接收。\n"
    total, cjk = document_char_count(document)
    assert total == len("标题岗位职责负责样本接收。")
    assert cjk == len("标题岗位职责负责样本接收")
    assert total > cjk  # 标点计入总字数，不计入汉字数


def test_sanitize_title_and_public_location():
    assert sanitize_title("迪安诊断医学检验技术员") == "医学检验技术员"
    assert sanitize_title("迪安（杭州）检验技术员") == "杭州检验技术员"
    assert public_location("杭州集团总部实验室") == "杭州实验室"
    assert public_location("杭州迪安医学检验实验室") == "杭州医学检验实验室"


def test_related_title_gate_rejects_unrelated_titles():
    assert is_related_title("检验技术员", "检验技师") is True
    assert is_related_title("医学检验技术员", "医学信息沟通专员") is False
    assert is_related_title("医学检验技术员", "实验室技术员") is True


# ---------------------------------------------------------------------------
# 自检：通过 / 常见问题
# ---------------------------------------------------------------------------


def test_compliant_jd_passes_quality_check():
    jd = _compliant_jd()
    document = render_jd_document(jd, external=True)
    report = validate_jd(jd, document=document, external=True)
    assert report.ok, [issue.code for issue in report.errors]
    assert report.duty_count == 6
    assert report.requirement_count == 6
    assert 400 <= report.total_chars <= 600
    assert set(report.ksao["学历专业"]) and report.ksao["职业素养"]


def test_template_duties_flagged_as_reference_and_weak_verb():
    class _Template:
        jd_id = "JD-ICL-实验室类-018"
        title = "检验技师"
        core_responsibilities = "1. 按照SOP完成临床检验标本的检测工作；2. 执行室内质控。"
        requirements_text = "学历：大专及以上"

    sparse = dict(SPARSE_FIELDS, core_responsibilities=[])
    jd = build_request_jd("REQ-TPL", sparse, related_jd=_Template())
    report = validate_jd(jd, external=False)
    codes = {issue.code for issue in report.issues}
    assert "duties_from_template" in codes
    assert "duty_weak_verb" in codes
    assert "duty_count_low" in codes


def test_sparse_jd_reports_length_and_count_errors():
    jd = build_request_jd("REQ-SPARSE", SPARSE_FIELDS)
    report = validate_jd(jd, external=False)
    codes = {issue.code for issue in report.errors}
    assert {"duty_count_low", "length_low", "requirement_count_low"} <= codes


def test_internal_mode_downgrades_compliance_to_warning():
    fields = dict(COMPLIANT_FIELDS, hard_requirements=[*REQUIREMENTS, "年龄：25-35岁"])
    jd = build_request_jd("REQ-AGE", fields)
    internal = validate_jd(jd, external=False)
    issue = next(issue for issue in internal.issues if issue.code == "sensitive_terms")
    assert issue.level == "warn"
    external = validate_jd(jd, external=True)
    assert "岁" not in render_jd_document(jd, external=True)
    dropped = next(issue for issue in external.issues if issue.code == "sensitive_terms_dropped")
    assert dropped.level == "warn"


def test_external_document_with_sensitive_text_is_error():
    jd = _compliant_jd()
    document = render_jd_document(jd, external=True) + "\n- 年龄：30岁\n"
    report = validate_jd(jd, document=document, external=True)
    issue = next(issue for issue in report.issues if issue.code == "sensitive_terms")
    assert issue.level == "error"


def test_external_document_hides_sensitive_and_company_info():
    fields = dict(
        COMPLIANT_FIELDS,
        position_title="迪安诊断医学检验技术员",
        hard_requirements=[*REQUIREMENTS, "年龄：25-35岁", "性别：不限"],
    )
    jd = build_request_jd("REQ-PUBLIC", fields)
    document = render_jd_document(jd, external=True)
    assert "迪安" not in document
    assert "集团总部" not in document
    assert "岁" not in document
    assert "性别" not in document
    # 对外版不暴露内部部门、紧急程度与岗位定位
    assert "临床检测中心" not in document
    assert "紧急" not in document
    assert "## 岗位定位" not in document


def test_external_document_reference_note_hides_internal_jd_id():
    class _Template:
        jd_id = "JD-ICL-实验室类-018"
        title = "检验技师"
        core_responsibilities = "1. 负责临床检验标本的检测工作；2. 执行室内质控与结果复核。"
        requirements_text = "学历：大专及以上"

    sparse = dict(SPARSE_FIELDS, core_responsibilities=[])
    jd = build_request_jd("REQ-REF", sparse, related_jd=_Template())
    document = render_jd_document(jd, external=True)
    assert "参考公司同岗位 JD 模板" in document
    assert "JD-ICL-实验室类-018" not in document


def test_ksao_groups_map_requirement_categories():
    jd = _compliant_jd()
    lanes = ksao_groups(jd)
    assert lanes["学历专业"] and lanes["知识技能"] and lanes["证书经验"] and lanes["职业素养"]
    assert any("检验士" in item for item in lanes["证书经验"])
    assert lanes["其他"] == []


def test_render_quality_report_contains_table_and_issues():
    jd = build_request_jd("REQ-SPARSE", SPARSE_FIELDS)
    report = validate_jd(jd, external=False)
    text = render_quality_report(report)
    assert "JD 撰写规范自检" in text
    assert "| 岗位职责 |" in text
    assert "必须修改（ERROR）" in text
    passed = render_quality_report(validate_jd(_compliant_jd(), external=True), external=True)
    assert "通过" in passed


# ---------------------------------------------------------------------------
# LLM 起草
# ---------------------------------------------------------------------------


def test_build_draft_prompt_contains_writing_rules():
    prompt = build_draft_prompt(COMPLIANT_FIELDS)
    assert "行业通用称谓" in prompt
    assert "强动词" in prompt
    assert "KSAO" in prompt
    assert "400–600" in prompt
    assert "不得出现性别、年龄、婚育" in prompt
    assert "医学检验技术员" in prompt
    assert '"responsibilities"' in prompt


def test_draft_merges_responsibilities_and_requirements():
    payload = {
        "title": "迪安医学检验技术员",
        "responsibilities": [
            "负责临床检验样本的接收、编号、离心、分装与保存管理。",
            "执行血常规、尿常规、生化、免疫等常规项目的检测与复核。",
            "负责检验仪器的日常操作、校准、维护与故障报修。",
            "执行室内质控与室间质评，记录并复核异常结果，及时上报。",
            "负责检验报告的录入、审核、发放与归档管理。",
        ],
        "requirements": [
            {"label": "学历专业", "text": "大专及以上，医学检验技术相关专业"},
            {"label": "知识技能", "text": "熟练操作血球仪、生化仪等常用设备"},
            {"label": "证书经验", "text": "持有检验士或检验师资格证"},
            {"label": "职业素养", "text": "责任心强，细致严谨，善于沟通协作"},
        ],
        "bonus_points": ["有分子检测经验者优先"],
    }
    analyzer = _FakeAnalyzer(payload)
    jd = draft_request_jd("REQ-DRAFT", SPARSE_FIELDS, analyzer=analyzer)
    assert jd["title"] == "医学检验技术员"
    assert jd["responsibilities"] == payload["responsibilities"]
    hard = [item["name"] for item in jd["hard_requirements"] if item["gate_type"] == "HARD_GATE"]
    assert "学历：大专及以上" in hard  # 用户原有硬条件保留
    assert "学历专业：大专及以上，医学检验技术相关专业" in hard
    assert jd["bonus_points"] == ["有分子检测经验者优先"]
    assert analyzer.prompts and "KSAO" in analyzer.prompts[0]


def test_draft_unavailable_on_invalid_payload():
    analyzer = _FakeAnalyzer({"title": "检验技术员", "responsibilities": []})
    with pytest.raises(JdDraftUnavailable):
        draft_request_jd("REQ-BAD", SPARSE_FIELDS, analyzer=analyzer)


def test_draft_unavailable_on_engine_error():
    analyzer = _FakeAnalyzer(error=HiringAnalysisError("HIRING_ANALYZER_CONFIG_MISSING"))
    with pytest.raises(JdDraftUnavailable):
        draft_request_jd("REQ-BAD", SPARSE_FIELDS, analyzer=analyzer)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _write_fields(path: Path, fields: dict[str, Any]) -> Path:
    path.write_text(json.dumps({"request_id": "REQ-CLI", "fields": fields}, ensure_ascii=False), encoding="utf-8")
    return path


def test_cli_check_passes_and_writes_quality_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    fields_path = _write_fields(tmp_path / "fields.json", COMPLIANT_FIELDS)
    out_dir = tmp_path / "out"
    rc = cli.main(["jd", "--fields-file", str(fields_path), "--external", "--check", "--out", str(out_dir)])
    assert rc == cli.EXIT_OK
    output = capsys.readouterr().out
    assert "结论 | 通过" in output
    assert (out_dir / "jd.quality.md").exists()
    assert (out_dir / "jd.quality.md").read_text(encoding="utf-8").startswith("### JD 撰写规范自检")


def test_cli_check_fails_with_quality_exit_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    fields_path = _write_fields(tmp_path / "fields.json", SPARSE_FIELDS)
    out_dir = tmp_path / "out"
    rc = cli.main(["jd", "--fields-file", str(fields_path), "--check", "--out", str(out_dir)])
    assert rc == cli.EXIT_QUALITY_ERROR
    assert "必须修改（ERROR）" in capsys.readouterr().out
    assert (out_dir / "jd.quality.md").exists()


def test_cli_external_prints_public_document(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    fields_path = _write_fields(tmp_path / "fields.json", COMPLIANT_FIELDS)
    rc = cli.main(["jd", "--fields-file", str(fields_path), "--external", "--no-write"])
    assert rc == cli.EXIT_OK
    output = capsys.readouterr().out
    assert output.startswith("# 医学检验技术员")
    assert "杭州实验室" in output
    assert "集团总部" not in output
    assert "## 岗位职责" in output


# ---------------------------------------------------------------------------
# 依据 JD 拆出的评分标签
# ---------------------------------------------------------------------------


def test_render_requirements_markdown_includes_scoring_tags_and_hard_filters():
    jd = _compliant_jd()
    doc = requirements_from_jd(jd)
    text = render_requirements_markdown(doc)
    scored = [
        row for row in doc["requirements"]
        if row["dimension"] in ("capability", "experience")
    ]
    assert scored
    assert "## 匹配标签（取自 JD：医学检验技术员，用于匹配候选人能力与经验）" in text
    assert "派生关系：JD 取自标准画像 + 本次实际需求" in text
    assert f"= {len(scored)} 条（命中率分母）" in text
    assert "### 能力（" in text
    assert "### 经验（" in text
    assert "| 编号 | 标签 | 级别 | 判据方式 | 简历命中线索 |" in text
    assert "### 硬性条件（不进命中率，单独核验）" in text
    assert "| 学历 |" in text and "| 证书 |" in text
    assert "不满足 → 不推荐" in text
    # 标签表是内部视图：不能带「可发布」的 JD 措辞
    assert "## 岗位职责" not in text
    assert "内部筛选视图" in text


def test_cli_tags_writes_internal_tag_file_and_keeps_jd_publishable(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    fields_path = _write_fields(tmp_path / "fields.json", COMPLIANT_FIELDS)
    out_dir = tmp_path / "out"
    rc = cli.main(["jd", "--fields-file", str(fields_path), "--tags", "--out", str(out_dir)])
    assert rc == cli.EXIT_OK
    output = capsys.readouterr().out
    assert "## 匹配标签（取自 JD" in output
    assert "计分标签" in output
    tags_path = out_dir / "jd.tags.md"
    assert tags_path.exists()
    assert "计分标签" in tags_path.read_text(encoding="utf-8")
    publishable = (out_dir / "jd.md").read_text(encoding="utf-8")
    # 两层结构：JD 正文保留任职要求原文，匹配标签只出现在独立的 tags 视图里
    assert "**技能与能力**" in publishable
    assert "计分标签" not in publishable
    assert "评分标签" not in publishable
    assert "简历命中线索" not in publishable


# ---------------------------------------------------------------------------
# 「最后呈现」：岗位职责 / 任职要求 / 匹配标签 三个大项
# ---------------------------------------------------------------------------


def test_presentation_has_three_sections_and_keeps_tags_out_of_requirements():
    jd = _compliant_jd()
    text = render_presentation(jd, external=True)
    headings = [line for line in text.splitlines() if line.startswith("## ")]
    assert len(headings) == 3, headings
    assert headings[0].startswith("## 岗位职责")
    assert headings[1].startswith("## 任职要求")
    assert headings[2].startswith("## 匹配标签（取自 JD：医学检验技术员")
    # 加分项折进任职要求，不再单列第四个大项
    assert "## 加分项" not in text
    assert "**加分项**" in text
    # 匹配标签绝不混进任职要求
    requirements_block = text.split("## 任职要求", 1)[1].split("## 匹配标签", 1)[0]
    for tag_name in ("快速学习能力", "规范操作与执行能力", "检验科经验", "团队合作能力"):
        assert tag_name not in requirements_block
    tags_block = text.split("## 匹配标签", 1)[1]
    # 聊天成稿里匹配标签只给名称：不要表格、判据、命中线索、特质/硬性条件
    assert "|" not in tags_block
    assert "计分标签" not in tags_block
    assert "判据" not in tags_block
    assert "命中线索" not in tags_block
    assert "硬性条件" not in tags_block
    assert "特质" not in tags_block
    assert "仪器操作能力" in tags_block
    assert "检验科经验" in tags_block
    tag_lines = [line for line in tags_block.splitlines() if line.strip()]
    assert tag_lines[0].startswith("（取自 JD：医学检验技术员")
    assert all(
        line.startswith(("- 能力：", "- 经验：")) for line in tag_lines[1:]
    ), tag_lines


def test_requirements_from_jd_uses_short_tag_names():
    jd = _compliant_jd()  # 没有匹配画像：走 JD 派生标签路径
    doc = requirements_from_jd(jd)
    names = [row["name"] for row in doc["requirements"]]
    assert names
    assert all(len(name) <= 14 and "。" not in name for name in names), names
    assert "分子或微生物检测经验" in names
    assert not any("执行血常规" in name for name in names)
    # 特质不生成标签：「职业素养」只留在 JD 任职要求里，不出现特质标签
    dims = {row["dimension"] for row in doc["requirements"]}
    assert dims <= {"capability", "experience"}, dims
    assert doc["dimension_counts"]["trait"] == 0
    assert not any(name in {"沟通表达能力", "细心", "责任心强"} for name in names)


def test_presentation_tag_names_match_scored_tags():
    """成稿的匹配标签必须与真正参与评分的标签一致（P0：显示 ≠ 评分已修）。"""
    profile = {
        "profile_id": "PROFILE-TEST",
        "name": "检验技术员（应届）",
        "key_abilities": [{"name": "快速学习能力"}, {"name": "规范操作与执行能力"}],
        "key_experiences": [{"name": "专业背景与项目经验"}, {"name": "检验科经验"}],
    }
    jd = build_request_jd("REQ-PROFILE", COMPLIANT_FIELDS, profile)
    text = render_presentation(jd, external=True)
    tags_block = text.split("## 匹配标签", 1)[1]
    displayed: set[str] = set()
    for line in tags_block.splitlines():
        if line.startswith(("- 能力：", "- 经验：")):
            displayed.update(part for part in line.split("：", 1)[1].split("、") if part)
    scored = {row["name"] for row in requirements_from_jd(jd)["requirements"]}
    assert displayed == scored
    # 画像里没有在本次 JD 中出现的标签不再硬塞进成稿
    assert "专业背景与项目经验" not in tags_block


def test_cli_presentation_writes_three_section_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    fields_path = _write_fields(tmp_path / "fields.json", COMPLIANT_FIELDS)
    out_dir = tmp_path / "out"
    rc = cli.main([
        "jd", "--fields-file", str(fields_path), "--external",
        "--presentation", "--out", str(out_dir),
    ])
    assert rc == cli.EXIT_OK
    output = capsys.readouterr().out
    headings = [line for line in output.splitlines() if line.startswith("## ")]
    assert len(headings) == 3, headings
    assert output.startswith("# 医学检验技术员")
    assert "计分标签" not in output
    presentation = (out_dir / "jd.presentation.md").read_text(encoding="utf-8")
    assert presentation.count("## ") >= 3
    assert "## 匹配标签（取自 JD" in presentation
    # 可发布 JD 不受影响：仍只有两节
    publishable = (out_dir / "jd.md").read_text(encoding="utf-8")
    assert "## 匹配标签" not in publishable
