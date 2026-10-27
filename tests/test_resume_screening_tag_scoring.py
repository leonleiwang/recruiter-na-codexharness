"""标签命中率评分测试：需求字段 → JD → 标签 → 简历命中率 + 年龄硬门槛。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from rn.resume_screening import cli, service
from rn.resume_screening.analysis import requirements
from rn.resume_screening.jd.generator import build_request_jd

FIELDS = {
    "position_title": "检验技术员",
    "location": "杭州市西湖区",
    "headcount": 2,
    "salary_range": "8-12K",
    "core_responsibilities": ["负责临检标本接收与上机检测", "负责室内质控与结果审核"],
    "key_experience_skills": ["PCR", "生化分析仪", "检验科经验"],
    "hard_requirements": [
        "大专及以上学历，医学检验技术专业",
        "持有检验士资格证",
        "年龄 25-35 岁",
        "接受体检季加班",
    ],
    "bonus_points": ["有三甲医院检验科经验"],
}

STRONG_RESUME = """张晓明 男 27岁
本科 医学检验技术 2018 年毕业
电话 13812345678 现居 杭州市西湖区
2019.07-2024.08 迪安诊断 检验技术员
负责临检标本接收、上机检测与室内质控、结果审核
熟悉 PCR、生化分析仪
持有检验师资格证
自我评价：工作细心严谨，抗压能力强，责任心强
"""

WEAK_RESUME = """李四 男 41岁
大专 市场营销
电话 13800000000 现居 北京市朝阳区
2015.01-2024.01 某贸易公司 销售代表
负责渠道拓展与客户维护，熟悉办公软件
"""


def _jd(fields: dict | None = None, request_id: str = "REQ-TAGS") -> dict:
    return build_request_jd(request_id, fields or FIELDS)


def _requirements_doc() -> dict:
    return requirements.requirements_from_jd(_jd())


# ---------------------------------------------------------------------------
# JD → 标签
# ---------------------------------------------------------------------------


def test_requirements_from_jd_splits_scored_tags():
    doc = _requirements_doc()
    assert doc["source"] == "jd"
    assert doc["jd_id"] == "JD-REQ-TAGS"
    assert doc["requirements"], "JD 必须拆出可计分标签"
    dims = {row["dimension"] for row in doc["requirements"]}
    assert dims <= {"capability", "experience"}
    assert all(row["requirement_id"].startswith("J") for row in doc["requirements"])
    names = " ".join(row["name"] for row in doc["requirements"])
    assert "检验科经验" in names
    assert "PCR" in names or "仪器操作能力" in names


def test_requirements_from_jd_maps_hard_requirements_to_filters():
    doc = _requirements_doc()
    hard = doc["hard_filters"]
    assert hard["location"] == "杭州市西湖区"
    assert "医学检验技术" in hard["education"]
    assert "检验士资格证" in hard["certification"]
    assert "25-35" in hard["age"]
    # 年龄转成硬性条件后，合规提醒里要说明用途
    assert any("年龄" in warning for warning in doc["warnings"])


def test_requirements_from_jd_bonus_points_become_preferred():
    doc = _requirements_doc()
    levels = {row["name"]: row["level"] for row in doc["requirements"]}
    bonus_rows = [name for name in levels if "三甲" in name]
    if bonus_rows:
        assert all(levels[name] == "优先" for name in bonus_rows)


def test_requirements_from_jd_keeps_other_hard_requirements_as_filters():
    doc = _requirements_doc()
    assert "体检季加班" in (doc["hard_filters"].get("other") or "")
    # 其他硬要求不进可计分标签：简历通常不会写「接受加班」这类意愿
    body = " ".join(row["name"] for row in doc["requirements"])
    assert "加班" not in body


def test_other_hard_requirement_is_pending_not_scored():
    matching = service.analyze_text(STRONG_RESUME, filename="zhang.txt", requirements_doc=_requirements_doc())[
        "matching"
    ]
    other_hints = [hint for hint in matching["hard_filter_hints"] if hint["filter"] == "other"]
    assert other_hints and all(hint["status"] == "待核实" for hint in other_hints)
    assert matching["disqualified"] is False


def test_requirements_from_jd_without_age_has_no_age_filter():
    fields = dict(FIELDS)
    fields["hard_requirements"] = ["持有检验士资格证"]
    doc = requirements.requirements_from_jd(_jd(fields))
    assert not doc["hard_filters"].get("age")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("年龄 25-35 岁", (25, 35)),
        ("年龄 25～35 周岁", (25, 35)),
        ("年龄不超过35周岁", (None, 35)),
        ("不超过35岁", (None, 35)),
        ("35 岁以下", (None, 35)),
        ("年龄25岁以上", (25, None)),
        ("2019-2022 年", (None, None)),
        ("", (None, None)),
    ],
)
def test_parse_age_requirement(text: str, expected: tuple[int | None, int | None]):
    assert requirements.parse_age_requirement(text) == expected


# ---------------------------------------------------------------------------
# 命中率评分 + 年龄硬门槛
# ---------------------------------------------------------------------------


def test_score_is_tag_hit_rate():
    report = service.analyze_text(STRONG_RESUME, filename="zhang.txt", requirements_doc=_requirements_doc())
    matching = report["matching"]
    assert matching["score_basis"] == "tag_hit_rate"
    assert matching["tag_applied_count"] > 0
    assert matching["tag_hit_count"] + matching["tag_weak_count"] <= matching["tag_applied_count"]
    expected = round(matching["tag_hit_count"] / matching["tag_applied_count"] * 100, 1)
    assert matching["overall_percent"] == expected
    assert matching["tag_hit_percent"] == expected
    assert matching["jd_id"] == "JD-REQ-TAGS"


def test_high_hit_rate_recommends_interview():
    matching = service.analyze_text(
        STRONG_RESUME, filename="zhang.txt", requirements_doc=_requirements_doc()
    )["matching"]
    assert matching["tag_hit_percent"] >= 40
    assert matching["recommendation"] == "推荐"


def test_low_hit_rate_is_pending_not_rejected():
    # 命中率不足四成的候选人不直接淘汰：结论是「待核实」，由面试/补材料再决定。
    weak = WEAK_RESUME.replace("41岁", "27岁")
    matching = service.analyze_text(weak, filename="weak.txt", requirements_doc=_requirements_doc())["matching"]
    assert matching["disqualified"] is False
    assert matching["tag_hit_percent"] < 40
    assert matching["recommendation"] == "待核实"
    assert any("待核实" in note for note in matching.get("notes") or [])


def test_age_within_range_is_not_disqualified():
    matching = service.analyze_text(STRONG_RESUME, filename="zhang.txt", requirements_doc=_requirements_doc())[
        "matching"
    ]
    age_hint = next(hint for hint in matching["hard_filter_hints"] if hint["filter"] == "age")
    assert age_hint["status"] == "满足"
    assert matching["disqualified"] is False


def test_age_over_limit_is_disqualified():
    old = STRONG_RESUME.replace("27岁", "41岁")
    report = service.analyze_text(old, filename="old.txt", requirements_doc=_requirements_doc())
    matching = report["matching"]
    assert matching["disqualified"] is True
    assert matching["recommendation"] == "不推荐"
    assert "不推荐" in matching["label"]
    assert "41" in matching["disqualification_reason"]
    age_hint = next(hint for hint in matching["hard_filter_hints"] if hint["filter"] == "age")
    assert age_hint["status"] == "不满足"
    assert age_hint["blocking"] is True
    markdown = service.render_report_markdown(report)
    assert "不推荐" in markdown
    assert "标签命中率" in markdown


def test_age_missing_is_pending_not_disqualified():
    no_age = STRONG_RESUME.replace("张晓明 男 27岁", "张晓明 男")
    matching = service.analyze_text(no_age, filename="noage.txt", requirements_doc=_requirements_doc())["matching"]
    assert matching["disqualified"] is False
    age_hint = next(hint for hint in matching["hard_filter_hints"] if hint["filter"] == "age")
    assert age_hint["status"] == "待核实"


def test_weak_resume_scores_lower_than_strong():
    weak = service.analyze_text(WEAK_RESUME, filename="weak.txt", requirements_doc=_requirements_doc())["matching"]
    strong = service.analyze_text(STRONG_RESUME, filename="strong.txt", requirements_doc=_requirements_doc())[
        "matching"
    ]
    assert strong["tag_hit_rate"] > weak["tag_hit_rate"]
    # 弱简历年龄 41 岁，触发硬门槛
    assert weak["disqualified"] is True


# ---------------------------------------------------------------------------
# CLI：analyze --fields-file / --jd-json
# ---------------------------------------------------------------------------


def _write_fields(path: Path) -> Path:
    path.write_text(json.dumps({"request_id": "REQ-CLI-TAGS", "fields": FIELDS}, ensure_ascii=False), encoding="utf-8")
    return path


def test_rank_key_puts_disqualified_candidates_last():
    from rn.resume_screening.analysis.matching import match_rank_key

    disqualified = {"disqualified": True, "tag_hit_rate": 1.0, "matched_must_have": [], "dimensions": {}}
    ok = {"disqualified": False, "tag_hit_rate": 0.2, "matched_must_have": [], "dimensions": {}}
    assert match_rank_key(ok) > match_rank_key(disqualified)


def test_cli_analyze_with_fields_file_scores_by_tags(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    fields_path = _write_fields(tmp_path / "fields.json")
    rc = cli.main(["analyze", "--text", STRONG_RESUME, "--fields-file", str(fields_path), "--no-write"])
    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "标签命中率" in out
    assert "JD-REQ-CLI-TAGS" in out


def test_cli_analyze_with_jd_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    jd_path = tmp_path / "jd.json"
    jd_path.write_text(json.dumps(_jd(), ensure_ascii=False), encoding="utf-8")
    rc = cli.main(["analyze", "--text", STRONG_RESUME, "--jd-json", str(jd_path), "--json", "--no-write"])
    assert rc == cli.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["matching"]["score_basis"] == "tag_hit_rate"
    assert payload["matching"]["jd_id"] == "JD-REQ-TAGS"


def test_cli_analyze_rejects_two_requirement_sources(tmp_path: Path):
    fields_path = _write_fields(tmp_path / "fields.json")
    jd_path = tmp_path / "jd.json"
    jd_path.write_text(json.dumps(_jd(), ensure_ascii=False), encoding="utf-8")
    rc = cli.main(["analyze", "--text", STRONG_RESUME, "--fields-file", str(fields_path), "--jd-json", str(jd_path)])
    assert rc == cli.EXIT_INPUT_ERROR


def test_cli_analyze_rejects_fields_with_jd_file(tmp_path: Path):
    fields_path = _write_fields(tmp_path / "fields.json")
    jd_file = tmp_path / "jd.txt"
    jd_file.write_text("负责临检标本检测", encoding="utf-8")
    rc = cli.main(["analyze", "--text", STRONG_RESUME, "--fields-file", str(fields_path), "--jd-file", str(jd_file)])
    assert rc == cli.EXIT_INPUT_ERROR


def test_cli_batch_ranks_by_tag_hit_rate(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    strong = tmp_path / "strong.txt"
    strong.write_text(STRONG_RESUME, encoding="utf-8")
    weak = tmp_path / "weak.txt"
    weak.write_text(WEAK_RESUME, encoding="utf-8")
    fields_path = _write_fields(tmp_path / "fields.json")
    rc = cli.main(["analyze", str(strong), str(weak), "--fields-file", str(fields_path), "--no-write"])
    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "标签命中率" in out
    assert "JD-REQ-CLI-TAGS" in out
    # 强简历排第一：命中率更高
    strong_pos = out.index("strong.txt") if "strong.txt" in out else out.index("张晓明")
    weak_pos = out.index("weak.txt") if "weak.txt" in out else out.index("李四")
    assert strong_pos < weak_pos


# ---------------------------------------------------------------------------
# 准确度修复：课程语境 / 证书全称 / 通用词 / 专业自动核验
# ---------------------------------------------------------------------------


def _tag_state(matching: dict, name: str) -> str:
    for block in matching["dimensions"].values():
        for item in block["items"]:
            if item["name"] == name:
                return item["state"]
    raise AssertionError(f"tag not found: {name}")


def _doc_with(**overrides) -> dict:
    fields = dict(FIELDS)
    fields.update(overrides)
    return requirements.requirements_from_jd(_jd(fields))


def test_course_lines_do_not_count_as_experience():
    """课程名（主修课程：临床分子生物学检验）不能当分子检测/检验科经验。"""
    resume = """赵六 女 24岁
本科 医学检验技术
主修课程：临床分子生物学检验、临床检验基础、免疫学检验
2024.07-2025.01 某生物科技公司 实验工程师
负责样本前处理与仪器日常维护
"""
    doc = _doc_with(bonus_points=["有分子检测经验者优先"])
    matching = service.analyze_text(resume, filename="zhao.txt", requirements_doc=doc)["matching"]
    assert _tag_state(matching, "分子或微生物检测经验") != "matched"
    assert _tag_state(matching, "检验科经验") != "matched"


def test_rotation_lines_count_as_experience_contact():
    """轮转接触过某类科室，按用户口径算经验证据（周清/王习之口径裁决）。"""
    resume = """钱八 男 24岁
本科 医学检验技术
2024.07-2025.06 某医学检验实验室 实习生
在实验室各分组轮转，包括采血、血液、免疫、微生物、PCR 等，实习 11 个月。
"""
    doc = _doc_with(bonus_points=["有分子检测经验者优先"])
    matching = service.analyze_text(resume, filename="qian.txt", requirements_doc=doc)["matching"]
    assert _tag_state(matching, "分子或微生物检测经验") == "matched"


def test_training_attendance_alone_is_pending_not_matched():
    """「参加学习培训/学习适应能力较好」这类泛泛表述最多待核实，不能堆词升级成命中。"""
    resume = """孙九 女 23岁
本科 医学检验技术
积极参加组织的学习培训活动，学习适应能力较好，继续学习意愿强。
"""
    doc = _doc_with(key_experience_skills=["快速学习能力"])
    matching = service.analyze_text(resume, filename="sun9.txt", requirements_doc=doc)["matching"]
    assert _tag_state(matching, "快速学习能力") != "matched"


def test_negative_statement_marks_contradicted():
    """简历明确写「没有/未接触过」才算未命中（有反证），没写只是待验证。"""
    resume = """钱十 女 24岁
本科 医学检验技术
本人没有分子检测经验，也未接触过微生物检测。
"""
    doc = _doc_with(bonus_points=["有分子检测经验者优先"])
    matching = service.analyze_text(resume, filename="qian10.txt", requirements_doc=doc)["matching"]
    assert _tag_state(matching, "分子或微生物检测经验") == "contradicted"
    assert matching["tag_contradicted_count"] >= 1


def test_fast_learning_self_claim_is_pending_not_matched():
    """自述「学习能力强/快速上手」不是命中，只算待验证线索；面试验证。"""
    resume = """吴十 女 23岁
本科 医学检验技术
本人学习能力强，能快速上手各类检验仪器，善于接受新事物。
"""
    doc = _doc_with(key_experience_skills=["快速学习能力"])
    matching = service.analyze_text(resume, filename="wu10.txt", requirements_doc=doc)["matching"]
    assert _tag_state(matching, "快速学习能力") != "matched"


def test_full_certificate_name_is_recognized():
    """「临床医学检验技术（士）资格证」应判为持有检验士证。"""
    resume = STRONG_RESUME.replace(
        "持有检验师资格证", "专业证书：临床医学检验技术（士）资格证"
    )
    matching = service.analyze_text(resume, filename="cert.txt", requirements_doc=_requirements_doc())[
        "matching"
    ]
    cert_hint = next(hint for hint in matching["hard_filter_hints"] if hint["filter"] == "certification")
    assert cert_hint["status"] == "满足"


def test_fast_learning_rank_below_top15_matches():
    """排名前 15% 及更靠前才算命中（不看 GPA 具体分数）。"""
    resume = """郑十一 男 22岁
本科 医学检验技术
专业排名前 5%，连续两年获校级奖学金。
"""
    doc = _doc_with(key_experience_skills=["快速学习能力"])
    matching = service.analyze_text(resume, filename="zheng11.txt", requirements_doc=doc)["matching"]
    assert _tag_state(matching, "快速学习能力") == "matched"


def test_fast_learning_rank_worse_than_top15_is_pending():
    """排名前 20%（未进前 15%）不能算命中；GPA 数值本身也不参与判定。"""
    resume = """冯十二 女 22岁
本科 医学检验技术
GPA 3.9，专业排名前 20%。
"""
    doc = _doc_with(key_experience_skills=["快速学习能力"])
    matching = service.analyze_text(resume, filename="feng12.txt", requirements_doc=doc)["matching"]
    assert _tag_state(matching, "快速学习能力") != "matched"


def test_certificate_alias_without_clinical_prefix():
    """「医学检验技术（士）资格证」（省略「临床」）也要识别为检验士证。"""
    resume = STRONG_RESUME.replace(
        "持有检验师资格证", "资格证书：医学检验技术（士）资格证"
    )
    matching = service.analyze_text(resume, filename="cert2.txt", requirements_doc=_requirements_doc())[
        "matching"
    ]
    cert_hint = next(hint for hint in matching["hard_filter_hints"] if hint["filter"] == "certification")
    assert cert_hint["status"] == "满足"


def test_health_inspection_certificate_is_not_clinical_technician():
    """「卫生检验士」属另一序列，不能当作临床检验士证判满足。"""
    resume = STRONG_RESUME.replace(
        "持有检验师资格证", "资格证书：卫生检验士资格证书"
    )
    matching = service.analyze_text(resume, filename="cert3.txt", requirements_doc=_requirements_doc())[
        "matching"
    ]
    cert_hint = next(hint for hint in matching["hard_filter_hints"] if hint["filter"] == "certification")
    assert cert_hint["status"] == "待核实"


def test_planned_exam_is_pending_not_satisfied():
    """「计划备考检验士资格证」是未取得语境，应判待核实。"""
    resume = STRONG_RESUME.replace(
        "持有检验师资格证", "计划备考检验士资格证"
    )
    matching = service.analyze_text(resume, filename="cert4.txt", requirements_doc=_requirements_doc())[
        "matching"
    ]
    cert_hint = next(hint for hint in matching["hard_filter_hints"] if hint["filter"] == "certification")
    assert cert_hint["status"] == "待核实"


def test_generic_words_alone_are_not_a_match():
    """只写「熟悉实验室日常工作」，没有具体仪器/上机动作，不能算仪器操作能力命中。"""
    resume = """孙七 男 25岁
本科 医学检验技术 2020 年毕业
2021.01-2024.01 某检测公司 检验员
熟悉实验室日常工作流程，负责标本接收与登记
"""
    matching = service.analyze_text(resume, filename="sun.txt", requirements_doc=_requirements_doc())[
        "matching"
    ]
    assert _tag_state(matching, "仪器操作能力") != "matched"


def test_major_condition_is_auto_checked_from_education():
    """JD 的「专业：…」进 education 硬条件，用简历学历段自动核验，不再永远待核实。"""
    doc = _doc_with(hard_requirements=[
        "大专及以上学历，医学检验技术专业",
        "持有检验士资格证",
        "专业：医学检验、医学检验技术、临床医学等专业",
    ])
    assert "专业" in doc["hard_filters"]["education"]
    assert "专业：" not in (doc["hard_filters"].get("other") or "")
    matching = service.analyze_text(STRONG_RESUME, filename="major.txt", requirements_doc=doc)["matching"]
    education_hint = next(hint for hint in matching["hard_filter_hints"] if hint["filter"] == "education")
    # 学历字段没抽到时仍是待核实，但专业已自动核验（不再落 other 永远待核实）
    assert "专业命中 医学检验技术" in education_hint["note"]


def test_traditional_chinese_resume_text_is_folded():
    """港台/繁体简历：常用繁体字折成简体后再匹配，词表只维护简体一份。"""
    resume = """陳十一 女 24歲
本科 醫學檢驗技術 2020 年畢業
2021.01-2025.06 某醫學檢驗所 檢驗科 檢驗員
"""
    doc = _doc_with(key_experience_skills=["检验科经验"])
    matching = service.analyze_text(resume, filename="trad.txt", requirements_doc=doc)["matching"]
    assert _tag_state(matching, "检验科经验") == "matched"


def test_abbreviation_counts_as_standard_operation():
    """同义/缩写覆盖：ISO15189、CNAS、LIS/HIS 这类写法要被认出来。"""
    resume = """李十二 男 25岁
本科 医学检验技术
在 ISO15189 认可实验室工作，参与室内质控与室间质评；用 LIS 系统录结果。
"""
    doc = _doc_with(key_experience_skills=["规范操作与执行能力", "仪器操作能力"])
    matching = service.analyze_text(resume, filename="abbr.txt", requirements_doc=doc)["matching"]
    assert _tag_state(matching, "规范操作与执行能力") == "matched"
    assert _tag_state(matching, "仪器操作能力") == "matched"


def test_interview_questions_are_generated_for_pending_items():
    """待验证项与待核实硬条件要能变成面试问题（报告里直接可问）。"""
    from rn.resume_screening.analysis import interview

    matching = {
        "verification_items": [
            {
                "name": "快速学习能力",
                "dimension": "capability",
                "state": "not_found",
                "state_label": "待验证（简历未提及）",
                "evidence_hints": "",
            }
        ],
        "hard_filter_hints": [
            {
                "filter": "certification",
                "requirement": "持有检验士资格证",
                "status": "待核实",
                "evidence": "医学检验技术(士)资格证",
            }
        ],
    }
    questions = interview.build_interview_questions(matching)
    assert [item["kind"] for item in questions] == ["tag", "hard"]
    assert questions[0]["question"].startswith("请举一个")
    assert "资格证" in questions[1]["question"]
    markdown = interview.render_interview_markdown(matching)
    assert "## 面试问题（按待验证项生成）" in markdown
    assert "简历线索：医学检验技术(士)资格证" in markdown
