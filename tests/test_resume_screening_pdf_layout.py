"""PDF 视觉排版相关的字段抽取回归测试。

BOSS 直聘等平台导出的 PDF 经过「按 block 坐标恢复阅读顺序」后，文本仍会保留
一些视觉排版特征：日期在条目前、标签被拆成多行（「姓」/「名：」）、值和一个
标签粘在同一行（「周英手机：」）、奖项和证书混在同一段。这些用例直接喂文本，
覆盖解析层的兜底逻辑，不依赖真实 PDF。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from rn.resume_screening.analysis.fields import extract_resume_fields  # noqa: E402


def _fields(text: str, *, source_name: str = "") -> dict:
    return extract_resume_fields(text, source_name=source_name)


# ---------------------------------------------------------------------------
# 教育：日期在前、条目不前
# ---------------------------------------------------------------------------


def test_education_date_before_entries_picks_highest_degree() -> None:
    text = (
        "基本信息\n姓名：张三\n"
        "教育背景\n"
        "2022.03-2024.07\n南方医科大学\n医学检验技术（本科）\n"
        "2018.09-2020.07\n肇庆医学高等专科学校\n医学检验技术（大专）\n"
        "主修课程：临床检验基础、微生物学检验\n"
    )
    fields = _fields(text)
    edu = fields["education"]
    assert edu["level"] == "本科"
    assert edu["school"] == "南方医科大学"
    assert edu["major"] == "医学检验技术"
    assert edu["graduation_year"] == 2024


def test_education_date_after_entries_still_works() -> None:
    text = (
        "教育经历\n"
        "大连大学\n本科\n医学检验技术\n2021.09-2025.06\n"
    )
    edu = _fields(text)["education"]
    assert edu["level"] == "本科"
    assert edu["school"] == "大连大学"
    assert edu["major"] == "医学检验技术"


def test_education_major_strips_trailing_zhuanye_and_empty_parens() -> None:
    text = "教育背景\n2020.09-2023.06\n洛阳职业技术学院\n医学检验技术专业\n"
    assert _fields(text)["education"]["major"] == "医学检验技术"

    text2 = "教育背景\n2020.09-2023.06\n洛阳职业技术学院\n医学检验技术（）\n"
    assert _fields(text2)["education"]["major"] == "医学检验技术"


# ---------------------------------------------------------------------------
# KV：标签碎片 / 值+标签粘连
# ---------------------------------------------------------------------------


def test_label_fragments_are_merged() -> None:
    text = "基本信息\n姓\n名：\n周英\n学\n历：\n大学本科\n年\n龄：\n23\n"
    fields = _fields(text)
    assert fields["name"] == "周英"
    assert fields["education"]["level"] == "本科"
    assert fields["age"] == 23


def test_value_and_next_label_glued_on_one_line() -> None:
    text = "基本信息\n姓名：\n周英手机：\n18869572962\n年龄：\n23\n"
    fields = _fields(text)
    assert fields["name"] == "周英"
    assert fields["phone"] == "18869572962"
    assert fields["age"] == 23


def test_label_only_line_is_not_a_name() -> None:
    fields = _fields("基本信息\n姓名：\n年龄：\n23\n")
    assert fields["name"] != "姓名"


def test_source_name_fallback_when_no_name_in_text() -> None:
    fields = _fields("基本信息\n性别：女\n", source_name="李四_检验技术员.pdf")
    assert fields["name"] == "李四"


# ---------------------------------------------------------------------------
# 证书：奖项/经历过滤、段头别名
# ---------------------------------------------------------------------------


def test_awards_in_cert_section_are_filtered() -> None:
    text = (
        "证书\n"
        "1.第五届“互联网+”创新创业大赛校赛优秀奖\n"
        "2.2019-2020学年度获得“优秀共青团员”\n"
        "3.临床医学检验技术（师）资格证\n"
    )
    certs = _fields(text)["certifications"]
    # 全角括号在 _normalize_text 中会被 NFKC 归一为半角
    assert "临床医学检验技术(师)资格证" in certs
    assert all("大赛" not in c and "优秀" not in c for c in certs)


def test_skills_certificate_section_header_maps_to_certs() -> None:
    text = "专业技能\n医学检验技术\n技能证书\n临床医学检验技术（师）资格证\n"
    assert "临床医学检验技术(师)资格证" in _fields(text)["certifications"]


def test_volunteer_section_does_not_pollute_certifications() -> None:
    text = (
        "证书\n驾驶证C1\n"
        "志愿者经历\n高速路口核酸采样\n2022.06-2022.08\n"
    )
    certs = _fields(text)["certifications"]
    assert certs == ["驾驶证C1"]


def test_cert_mention_across_wrapped_lines_is_extracted() -> None:
    text = (
        "学术成就：专业成绩排名前5%，取得《临床医学检验技\n"
        "术（师）》资格证书，具备独立开展常规检验项目的从业资质。\n"
    )
    certs = _fields(text)["certifications"]
    assert any("资格证" in c and "检验技术" in c for c in certs)


# ---------------------------------------------------------------------------
# OCR 拆字修复
# ---------------------------------------------------------------------------


def test_ocr_split_surname_is_repaired() -> None:
    fields = _fields("基本信息\n姓名：弓长雨\n")
    assert fields["name"] == "张雨"
