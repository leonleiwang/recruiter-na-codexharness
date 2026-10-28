"""resume_screening.analysis.fields 单测：简历文本 → 固定字段 dict。

覆盖：中文 KV 全字段、OCR 空格/拆字容错、英文简历、分段式工作经历、应届生、
极简教育简历、乱文本不编造、表格式「姓名 | 张三」、学历归一化、`至今` 日期、
``source_name`` 兜底、证书/技能/自我评价/项目段落。

本机 pytest 的 ``tmp_path`` 有 Windows ACL 问题（PermissionError WinError 5），
本文件不使用任何临时目录，也不访问网络。
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _path in (str(ROOT / "runtime"), str(ROOT / "workspace")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from rn.resume_screening.analysis.fields import (  # noqa: E402
    RESUME_FIELD_KEYS,
    extract_resume_fields,
    looks_like_resume,
)

EXPECTED_KEYS = (
    "name",
    "phone",
    "email",
    "gender",
    "age",
    "location",
    "education",
    "total_work_years",
    "current_company",
    "current_position",
    "expected_position",
    "expected_city",
    "expected_salary",
    "certifications",
    "skills",
    "industry_tags",
    "work_experiences",
    "projects",
    "self_evaluation",
    "raw_text",
    "missing_fields",
    "extraction_notes",
)
CONTENT_KEYS = set(EXPECTED_KEYS) - {"raw_text", "missing_fields", "extraction_notes"}
EDUCATION_KEYS = {"level", "degree", "school", "major", "graduation_year"}
WORK_KEYS = {"company", "position", "start_date", "end_date", "description", "months", "duration_estimated"}


def _current_month() -> str:
    today = dt.date.today()
    return f"{today.year:04d}-{today.month:02d}"


def _months_since(year: int, month: int) -> int:
    today = dt.date.today()
    return (today.year - year) * 12 + (today.month - month) + 1


CHINESE_KV_RESUME = """姓名：张三
性别：男
年龄：28
联系电话：13812345678
电子邮箱：zhangsan@example.com
现居住地：上海市
最高学历：本科
毕业院校：复旦大学
专业：计算机科学与技术
毕业时间：2019-06
工作年限：5年
求职意向：Java开发工程师
期望薪资：15K-25K
期望城市：上海

工作经历
2019.07-2021.06 上海某某科技有限公司 后端开发工程师
负责订单系统接口开发与维护，参与需求评审。

2021.07-至今 杭州某某网络科技有限公司 高级后端开发工程师
负责支付网关重构，QPS 提升 3 倍。
"""

OCR_RESUME = """个 人 简 历
姓 名：李 四
性 别：女
年 龄：30
手 机：13900001111
电子邮箱：lisi@163.com
学 历：大学本科
毕 业 院 校：北京大学
专 业：临床医学
工 作 经 历
2018.07 - 2021.08 北京协和医院 检氵则技术员
负责临床检验科日常检氵则工作。
"""

# Windows OCR 图片简历：多个标签挤在同一行，且冒号常被识别成「．」
INLINE_LABEL_OCR_RESUME = """张晓明
电话：13812345678 邮箱：zhangxm@example.com
性别：男年龄：27 现居地：杭州市西湖区
学历：本科专业．医学检验技术毕业院校．浙江中医药大学毕业年份：2021
工作年限：3 年期望城市．杭州期望薪资：10-12 K 求职意向．检验技术员
证书：检验师资格证技能：PCR、生化分析仪、室内质控
工作经历
2021.07-2024.08 杭州迪安医学检验所检验技术员
负责临检标本接收、上机检氵则、室内质控与结果审核，带教 2 名实习生，体检季日样本量 800 +。
自我评价
工作细心严谨，能承受体检季高强度加班，主动复盘异常案例，责任心强。
"""

ENGLISH_RESUME = """John Smith
Email: john.smith@example.com | Phone: +86 138 0000 1234
Location: Shanghai
Objective: Senior Python Developer
Expected Salary: 30K-40K

Education
Fudan University
Bachelor of Computer Science
2015.09 - 2019.06

Work Experience
2021.03 - Present  ByteDance  Senior Python Developer
Built data pipelines for recommendation systems.

2019.07 - 2021.02  Alibaba  Python Developer
Developed internal tools.

Skills
Python, SQL, Docker, Kubernetes
"""

WORK_BLOCK_RESUME = """姓名：钱九
电话：13300006666
工作经历
娄底市第一人民医院 医学检验
2021.05-2022.03
负责临床检验科日常检测工作，独立完成生化与免疫项目。

娄底市中医医院 医学检验
2023.01-2024.02
负责门诊检验标本接收与结果审核。
"""

FRESH_GRADUATE_RESUME = """姓名：王五
性别：男
年龄：22
电话：13700002222
邮箱：wangwu@qq.com
教育背景
2021.09-2025.06 华中科技大学 本科 软件工程
求职意向：软件测试工程师
"""

MINIMAL_RESUME = """姓名：赵六
电话：13600003333
教育背景
2018.09-2021.06 江城职业技术学院 大专 护理学
"""

NOISE_TEXT = "今天天气不错，我去超市买了苹果和香蕉。价格是 12.5 元。"

TABLE_RESUME = """姓名 | 陈七
性别 | 男
年龄 | 35
电话 | 13500004444
邮箱 | chenqi@126.com
最高学历 | 研究生
毕业院校 | 上海交通大学
专业 | 机械工程
工作年限 | 8年
求职意向 | 机械设计工程师
工作经历
2016.07-至今 上海某某机械有限公司 机械设计工程师
负责减速机结构设计。
"""

SECTIONS_RESUME = """姓名：吴十
证书
大学英语六级
专业技能
熟悉 PCR、生化分析仪
个人评价
工作认真细致，责任心强。
项目经验
项目名称：检验流程优化
2023.01-2023.06
负责检验科标本流转流程改造。
项目二：LIS 系统上线
负责 LIS 系统上线测试与培训。
"""


def test_result_keys_contract():
    assert RESUME_FIELD_KEYS == EXPECTED_KEYS
    result = extract_resume_fields(CHINESE_KV_RESUME)
    assert tuple(result) == EXPECTED_KEYS
    assert result["raw_text"] == CHINESE_KV_RESUME
    assert set(result["education"]) == EDUCATION_KEYS
    assert isinstance(result["total_work_years"], int)
    assert isinstance(result["education"]["graduation_year"], int)
    for entry in result["work_experiences"]:
        assert set(entry) == WORK_KEYS
        assert isinstance(entry["months"], int)
        assert isinstance(entry["duration_estimated"], bool)
    assert isinstance(result["certifications"], list)
    assert isinstance(result["skills"], list)
    assert isinstance(result["industry_tags"], list)
    assert isinstance(result["missing_fields"], list)
    assert isinstance(result["extraction_notes"], list)


def test_empty_text_returns_fixed_blank_shape():
    result = extract_resume_fields("")
    assert result["name"] == ""
    assert result["phone"] == ""
    assert result["email"] == ""
    assert result["gender"] == ""
    assert result["age"] is None
    assert result["location"] == ""
    assert result["education"] == {
        "level": "",
        "degree": "",
        "school": "",
        "major": "",
        "graduation_year": None,
    }
    assert result["total_work_years"] == 0
    assert result["current_company"] == ""
    assert result["current_position"] == ""
    assert result["expected_position"] == ""
    assert result["expected_city"] == ""
    assert result["expected_salary"] == ""
    assert result["certifications"] == []
    assert result["skills"] == []
    assert result["industry_tags"] == []
    assert result["work_experiences"] == []
    assert result["projects"] == []
    assert result["self_evaluation"] == ""
    assert result["raw_text"] == ""
    assert set(result["missing_fields"]) == CONTENT_KEYS
    assert any("输入为空" in note for note in result["extraction_notes"])


def test_chinese_kv_full_extraction():
    result = extract_resume_fields(CHINESE_KV_RESUME)
    assert result["name"] == "张三"
    assert result["phone"] == "13812345678"
    assert result["email"] == "zhangsan@example.com"
    assert result["gender"] == "男"
    assert result["age"] == 28
    assert result["location"] == "上海"
    assert result["education"]["level"] == "本科"
    assert result["education"]["school"] == "复旦大学"
    assert result["education"]["major"] == "计算机科学与技术"
    assert result["education"]["graduation_year"] == 2019
    assert result["total_work_years"] == 5
    assert result["expected_position"] == "Java开发工程师"
    assert result["expected_city"] == "上海"
    assert result["expected_salary"] == "15K-25K"
    assert result["current_company"] == "杭州某某网络科技有限公司"
    assert result["current_position"] == "高级后端开发工程师"
    assert len(result["work_experiences"]) == 2
    first, second = result["work_experiences"]
    assert first["company"] == "上海某某科技有限公司"
    assert first["position"] == "后端开发工程师"
    assert first["start_date"] == "2019-07"
    assert first["end_date"] == "2021-06"
    assert first["months"] == 24
    assert "订单系统接口开发" in first["description"]
    assert second["company"] == "杭州某某网络科技有限公司"
    assert second["position"] == "高级后端开发工程师"
    assert second["start_date"] == "2021-07"
    assert second["end_date"] == _current_month()
    assert second["months"] == _months_since(2021, 7)
    assert "支付网关重构" in second["description"]
    assert set(result["missing_fields"]) == {
        "certifications",
        "skills",
        "industry_tags",
        "projects",
        "self_evaluation",
    }


def test_ocr_spaces_and_broken_chars_are_tolerated():
    result = extract_resume_fields(OCR_RESUME)
    assert result["name"] == "李四"
    assert result["phone"] == "13900001111"
    assert result["email"] == "lisi@163.com"
    assert result["gender"] == "女"
    assert result["age"] == 30
    assert result["education"]["level"] == "本科"
    assert result["education"]["school"] == "北京大学"
    assert result["education"]["major"] == "临床医学"
    assert result["current_company"] == "北京协和医院"
    # OCR 把「测」拆成「氵则」，归一化后应还原成「检测技术员」
    assert result["current_position"] == "检测技术员"
    assert len(result["work_experiences"]) == 1
    entry = result["work_experiences"][0]
    assert entry["company"] == "北京协和医院"
    assert entry["position"] == "检测技术员"
    assert entry["start_date"] == "2018-07"
    assert entry["end_date"] == "2021-08"
    assert entry["months"] == 38
    assert "检测工作" in entry["description"]
    notes = "\n".join(result["extraction_notes"])
    assert "归一化" in notes
    assert "CJK 字符间空格" in notes
    assert "OCR 拆字" in notes


def test_inline_labels_on_one_ocr_line():
    # 回归：OCR 把「性别：男年龄：27」「证书：X技能：Y」挤在同一行；
    # 值必须在下一个已知标签处截断，不能取到行尾（否则证书会把技能吞掉）。
    result = extract_resume_fields(INLINE_LABEL_OCR_RESUME)
    assert result["name"] == "张晓明"
    assert result["phone"] == "13812345678"
    assert result["email"] == "zhangxm@example.com"
    # 同一行多标签分别抽取
    assert result["gender"] == "男"
    assert result["age"] == 27
    assert result["location"] == "杭州市西湖区"
    # 「．」当冒号用的一行多标签
    assert result["education"]["level"] == "本科"
    assert result["education"]["school"] == "浙江中医药大学"
    assert result["education"]["major"] == "医学检验技术"
    assert result["education"]["graduation_year"] == 2021
    assert result["total_work_years"] == 3
    assert result["expected_position"] == "检验技术员"
    assert result["expected_city"] == "杭州"
    assert result["expected_salary"] == "10-12 K"
    # 证书值止于「技能：」，不能吞入技能内容
    assert result["certifications"] == ["检验师资格证"]
    assert result["skills"] == ["PCR", "生化分析仪", "室内质控"]
    assert len(result["work_experiences"]) == 1
    entry = result["work_experiences"][0]
    assert entry["company"] == "杭州迪安医学检验所"
    assert entry["position"] == "检验技术员"
    assert entry["start_date"] == "2021-07"
    assert entry["end_date"] == "2024-08"
    assert entry["description"].startswith("负责临检标本接收")
    assert result["self_evaluation"].startswith("工作细心严谨")


def test_english_resume():
    result = extract_resume_fields(ENGLISH_RESUME)
    assert result["name"] == "John Smith"
    assert result["phone"] == "13800001234"
    assert result["email"] == "john.smith@example.com"
    assert result["location"] == "Shanghai"
    assert result["education"]["level"] == "本科"
    assert result["education"]["degree"] == "学士"
    assert result["education"]["school"] == "Fudan University"
    assert result["education"]["major"] == "Computer Science"
    assert result["education"]["graduation_year"] == 2019
    assert result["expected_position"] == "Senior Python Developer"
    assert result["expected_salary"] == "30K-40K"
    assert result["current_company"] == "ByteDance"
    assert result["current_position"] == "Senior Python Developer"
    assert result["skills"] == ["Python", "SQL", "Docker", "Kubernetes"]
    assert result["total_work_years"] == 7
    assert len(result["work_experiences"]) == 2
    latest = result["work_experiences"][0]
    assert latest["company"] == "ByteDance"
    assert latest["position"] == "Senior Python Developer"
    assert latest["start_date"] == "2021-03"
    assert latest["end_date"] == _current_month()
    earlier = result["work_experiences"][1]
    assert earlier["company"] == "Alibaba"
    assert earlier["position"] == "Python Developer"
    assert earlier["start_date"] == "2019-07"
    assert earlier["end_date"] == "2021-02"
    assert earlier["months"] == 20


def test_work_experience_block_format():
    result = extract_resume_fields(WORK_BLOCK_RESUME)
    entries = result["work_experiences"]
    assert len(entries) == 2
    first, second = entries
    assert first["company"] == "娄底市第一人民医院"
    assert first["position"] == "医学检验"
    assert first["start_date"] == "2021-05"
    assert first["end_date"] == "2022-03"
    assert first["months"] == 11
    assert "独立完成生化与免疫项目" in first["description"]
    assert "娄底市中医医院" not in first["description"]
    assert second["company"] == "娄底市中医医院"
    assert second["position"] == "医学检验"
    assert second["start_date"] == "2023-01"
    assert second["end_date"] == "2024-02"
    assert second["months"] == 14
    assert "门诊检验标本接收" in second["description"]
    assert "娄底市第一人民医院" not in second["description"]
    assert result["current_company"] == "娄底市中医医院"
    assert result["current_position"] == "医学检验"
    assert result["total_work_years"] == 2


def test_fresh_graduate_has_zero_work_years():
    result = extract_resume_fields(FRESH_GRADUATE_RESUME)
    assert result["name"] == "王五"
    assert result["education"]["level"] == "本科"
    assert result["education"]["school"] == "华中科技大学"
    assert result["education"]["major"] == "软件工程"
    assert result["education"]["graduation_year"] == 2025
    assert result["work_experiences"] == []
    assert result["total_work_years"] == 0
    assert "total_work_years" in result["missing_fields"]
    assert result["current_company"] == ""
    assert result["current_position"] == ""
    assert result["expected_position"] == "软件测试工程师"


def test_minimal_education_only_resume_does_not_fabricate():
    result = extract_resume_fields(MINIMAL_RESUME)
    assert result["name"] == "赵六"
    assert result["phone"] == "13600003333"
    assert result["education"] == {
        "level": "大专",
        "degree": "",
        "school": "江城职业技术学院",
        "major": "护理学",
        "graduation_year": 2021,
    }
    assert result["email"] == ""
    assert result["gender"] == ""
    assert result["age"] is None
    assert result["location"] == ""
    assert result["certifications"] == []
    assert result["skills"] == []
    assert result["work_experiences"] == []
    assert result["current_company"] == ""
    assert result["expected_position"] == ""
    assert "education" not in result["missing_fields"]
    assert {"email", "gender", "age", "location"} <= set(result["missing_fields"])


def test_noise_text_does_not_fabricate_fields():
    result = extract_resume_fields(NOISE_TEXT)
    assert result["name"] == ""
    assert result["phone"] == ""
    assert result["email"] == ""
    assert result["age"] is None
    assert result["education"]["level"] == ""
    assert result["education"]["school"] == ""
    assert result["work_experiences"] == []
    assert result["certifications"] == []
    assert result["skills"] == []
    assert set(result["missing_fields"]) == CONTENT_KEYS
    assert result["raw_text"] == NOISE_TEXT


def test_table_pipe_format():
    result = extract_resume_fields(TABLE_RESUME)
    assert result["name"] == "陈七"
    assert result["gender"] == "男"
    assert result["age"] == 35
    assert result["phone"] == "13500004444"
    assert result["email"] == "chenqi@126.com"
    assert result["education"]["level"] == "硕士"
    assert result["education"]["school"] == "上海交通大学"
    assert result["education"]["major"] == "机械工程"
    # 显式的「工作年限 | 8年」优先于按起止日期估算
    assert result["total_work_years"] == 8
    assert result["expected_position"] == "机械设计工程师"
    assert result["current_company"] == "上海某某机械有限公司"
    assert result["current_position"] == "机械设计工程师"
    assert len(result["work_experiences"]) == 1
    assert result["work_experiences"][0]["start_date"] == "2016-07"
    assert result["work_experiences"][0]["months"] >= 100


def test_sections_certifications_skills_evaluation_projects():
    result = extract_resume_fields(SECTIONS_RESUME)
    assert result["name"] == "吴十"
    assert result["certifications"] == ["大学英语六级"]
    assert "熟悉 PCR" in result["skills"]
    assert "生化分析仪" in result["skills"]
    assert "工作认真细致" in result["self_evaluation"]
    assert "责任心强" in result["self_evaluation"]
    names = [project["name"] for project in result["projects"]]
    assert "检验流程优化" in names
    assert "LIS 系统上线" in names
    first = next(p for p in result["projects"] if p["name"] == "检验流程优化")
    assert "2023.01-2023.06" in first["description"]
    assert "标本流转流程改造" in first["description"]


def test_looks_like_resume_gate():
    assert looks_like_resume(CHINESE_KV_RESUME) is True
    assert looks_like_resume(WORK_BLOCK_RESUME) is True
    assert looks_like_resume(ENGLISH_RESUME) is True
    assert looks_like_resume(NOISE_TEXT) is False
    assert looks_like_resume("") is False
    assert looks_like_resume("今天天气不错。") is False


@pytest.mark.parametrize(
    ("raw_level", "expected_level"),
    [
        ("研究生", "硕士"),
        ("专升本", "本科"),
        ("大学本科", "本科"),
        ("专科", "大专"),
        ("本科", "本科"),
        ("硕士", "硕士"),
        ("博士", "博士"),
        ("中专", "中专"),
    ],
)
def test_education_level_normalization(raw_level: str, expected_level: str):
    result = extract_resume_fields(f"姓名：测试\n最高学历：{raw_level}\n")
    assert result["education"]["level"] == expected_level


def test_present_end_date_and_date_formats():
    current = _current_month()
    text = (
        "姓名：周清\n"
        "工作经历\n"
        "2024.07 - 至今\n"
        "杭州艾迪康医学检验中心有限公司\n"
        "检验技术员\n"
        "负责临检组日常检测工作。\n"
    )
    result = extract_resume_fields(text)
    assert len(result["work_experiences"]) == 1
    entry = result["work_experiences"][0]
    assert entry["company"] == "杭州艾迪康医学检验中心有限公司"
    assert entry["position"] == "检验技术员"
    assert entry["start_date"] == "2024-07"
    assert entry["end_date"] == current
    assert entry["months"] == _months_since(2024, 7)

    chinese_date = extract_resume_fields(
        "姓名：甲\n工作经历\n2021年7月-2024年8月 某公司 检验员\n负责检验。\n"
    )
    chinese_entry = chinese_date["work_experiences"][0]
    assert chinese_entry["start_date"] == "2021-07"
    assert chinese_entry["end_date"] == "2024-08"
    assert chinese_entry["months"] == 38

    slash_date = extract_resume_fields("姓名：乙\n工作经历\n2021/07~/2024/08 某公司 检验员\n")
    slash_entry = slash_date["work_experiences"][0]
    assert slash_entry["start_date"] == "2021-07"
    assert slash_entry["end_date"] == "2024-08"

    salary = extract_resume_fields("姓名：丁\n期望薪资：8-12K\n期望城市：杭州\n")
    assert salary["expected_salary"] == "8-12K"
    assert salary["expected_city"] == "杭州"


def test_source_name_fallback_for_missing_name():
    result = extract_resume_fields("教育背景：某大学 本科\n", source_name="张三-检验技师简历.pdf")
    assert result["name"] == "张三"
    assert any("来源文件名" in note for note in result["extraction_notes"])


def test_missing_fields_and_notes_contract():
    text = "姓名：王五\n电话：13700002222\n"
    result = extract_resume_fields(text)
    assert result["name"] == "王五"
    assert "email" in result["missing_fields"]
    assert "work_experiences" in result["missing_fields"]
    assert "raw_text" not in result["missing_fields"]
    assert "missing_fields" not in result["missing_fields"]
    assert "extraction_notes" not in result["missing_fields"]
    assert result["raw_text"] == text
    assert result["extraction_notes"]
    assert all(isinstance(note, str) and note for note in result["extraction_notes"])
    assert any("缺失字段" in note for note in result["extraction_notes"])
