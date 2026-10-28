"""单元测试：简历 → 三维度标签 → 岗位要求 → 匹配度（analysis 层）。

不依赖 ingest（文件解析）与 fields（字段抽取）的实现：本文件用合成 fields dict
固定输入，保证匹配逻辑本身可独立验证。
"""
from __future__ import annotations

from rn.resume_screening.analysis import cues, matching, requirements, tags, textutil

STRONG_FIELDS = {
    "name": "张晓明",
    "phone": "13812345678",
    "location": "杭州市西湖区",
    "education": {"level": "本科", "school": "浙江中医药大学", "major": "医学检验技术"},
    "total_work_years": 3,
    "current_company": "杭州迪安医学检验所",
    "current_position": "检验技术员",
    "expected_city": "杭州",
    "certifications": ["检验师资格证"],
    "skills": ["PCR", "生化分析仪", "室内质控"],
    "work_experiences": [{
        "company": "杭州迪安医学检验所",
        "position": "检验技术员",
        "start_date": "2021-07",
        "end_date": "2024-08",
        "description": "负责临检标本接收、上机检测、室内质控与结果审核，带教2名实习生，体检季日样本量800+。",
    }],
    "self_evaluation": "工作细心严谨，能承受体检季高强度加班，主动复盘异常案例，责任心强。",
    "raw_text": (
        "姓名：张晓明 电话：13812345678\n"
        "学历：本科 专业：医学检验技术 毕业院校：浙江中医药大学\n"
        "现居地：杭州市西湖区 工作年限：3年\n"
        "2021.07-2024.08 杭州迪安医学检验所 检验技术员\n"
        "负责临检标本接收、上机检测、室内质控与结果审核，带教2名实习生，体检季日样本量800+。\n"
        "技能：PCR、生化分析仪、室内质控\n"
        "自我评价：工作细心严谨，能承受体检季高强度加班，主动复盘异常案例，责任心强。"
    ),
    "missing_fields": [],
}

WEAK_FIELDS = {
    "name": "李子涵",
    "phone": "13900001111",
    "location": "杭州市余杭区",
    "education": {"level": "大专", "school": "某职业技术学院", "major": "文秘"},
    "total_work_years": 0,
    "certifications": [],
    "skills": ["Office"],
    "work_experiences": [],
    "self_evaluation": "性格开朗，乐于沟通。",
    "raw_text": "姓名：李子涵 学历：大专 专业：文秘 现居地：杭州市余杭区 技能：Office 自我评价：性格开朗，乐于沟通。",
    "missing_fields": ["email"],
}

JD_TEXT = """
岗位职责：
1. 负责检验标本的接收、上机检测、结果审核与报告发布，执行室内质控与室间质评；
2. 负责试剂领用、库存核对、效期管理，能在试剂更换时独立完成定标与质控验证；
3. 参与新员工带教与流程优化。

任职要求：
1. 大专及以上学历，医学检验技术/临床医学/生物技术等相关专业；
2. 1年以上医院检验科或第三方医学检验所检验工作经历；
3. 持有检验士/检验师资格证；
4. 熟悉生化分析仪、PCR等仪器的操作；
5. 细心严谨、责任心强，能承受高峰期工作压力；
6. 有带教经验者优先。
"""


def test_cue_table_covers_three_dimensions():
    assert cues.DIMENSIONS == ("capability", "experience", "trait")
    per_dimension = {dim: len(cues.CUES_BY_DIMENSION[dim]) for dim in cues.DIMENSIONS}
    assert per_dimension["capability"] >= 7
    assert per_dimension["experience"] >= 4
    assert per_dimension["trait"] == 5


def test_cue_strong_keywords_strip_bang():
    entry = cues.find_cue("capability", "规范操作与执行能力")
    assert entry is not None
    assert "室内质控" in entry.strong_keywords
    assert all(not kw.startswith("!") for kw in entry.strong_keywords)
    assert all(not kw.startswith("!") for kw in entry.all_keywords())


def test_match_cues_and_canonicalize():
    hits = cues.match_cues("负责临检标本接收、上机检测与室内质控", dimensions=("capability",))
    names = {row["name"] for row in hits}
    assert "规范操作与执行能力" in names
    assert cues.canonicalize("负责带教2名实习生", dimension="experience") == "人员带教经验"
    assert cues.canonicalize("熟悉生化分析仪操作", dimension="capability") == "仪器操作能力"


def test_requirements_from_policy_counts_and_keywords():
    doc = requirements.requirements_from_policy(profile_id="PROFILE-412167F2937C")
    assert doc["source"] == "policy"
    assert doc["dimension_counts"] == {"capability": 6, "experience": 3, "trait": 4}
    assert len(doc["requirements"]) == 13
    by_name = {row["name"]: row for row in doc["requirements"]}
    # 政策 hints 里的短语不能当匹配关键词（否则「室间质评」会误命中试剂管理能力）
    assert "室间质评" not in by_name["试剂耗材管理能力"]["keywords"]
    assert "室内质控" in by_name["规范操作与执行能力"]["keywords"]
    assert by_name["检验科经验"]["level"] == "必须"
    assert by_name["人员带教经验"]["level"] == "优先"


def test_requirements_policy_fresh_graduate_override():
    doc = requirements.requirements_from_policy(profile_id="PROFILE-91603AA55F6E")
    assert len(doc["requirements"]) == 11
    assert len(doc["excluded"]) == 5
    names = {row["name"] for row in doc["requirements"]}
    assert "人员带教经验" not in names
    assert "试剂管理经验" not in names


def test_split_jd_lines_strips_headers_and_bullets():
    lines = requirements.split_jd_lines(JD_TEXT)
    assert lines
    assert all("岗位职责" not in line and "任职要求" not in line for line in lines)
    assert any(line.startswith("负责检验标本的接收") for line in lines)


def test_requirements_from_jd_text_classifies_and_extracts_hard_filters():
    doc = requirements.requirements_from_jd_text(JD_TEXT, job_name="检验技术员")
    assert doc["source"] == "jd_text"
    names = {row["name"]: row for row in doc["requirements"]}
    # 职责句按能力归类，不能被「审核」误判成特质
    assert names["规范操作与执行能力"]["dimension"] == "capability"
    assert names["检验科经验"]["dimension"] == "experience"
    assert names["人员带教经验"]["dimension"] == "experience"
    assert names["细心"]["dimension"] == "trait"
    # 学历 / 证书进硬性条件，不占三维额度
    assert set(doc["hard_filters"]) == {"education", "certification"}
    assert all(row["name"] not in ("大专及以上学历，医学检验技术/临床医学/生物技术等相关专业",)
               for row in doc["requirements"])
    assert len(doc["requirements"]) == 6


def test_extract_resume_tags_strong_and_weak():
    strong = tags.extract_resume_tags(STRONG_FIELDS)
    weak = tags.extract_resume_tags(WEAK_FIELDS)
    strong_names = {tag["name"] for dim_rows in strong["tags"].values() for tag in dim_rows}
    weak_names = {tag["name"] for dim_rows in weak["tags"].values() for tag in dim_rows}
    assert {"规范操作与执行能力", "检验科经验", "人员带教经验", "细心", "责任心"} <= strong_names
    assert "检验科经验" not in weak_names
    for dim_rows in strong["tags"].values():
        for tag in dim_rows:
            assert 0.0 <= float(tag["confidence"]) <= 1.0
            assert tag["tag_id"].startswith("T-")
    assert str(strong["summary"]["name"]) == "张晓明"


def test_match_report_prefers_strong_resume():
    doc = requirements.requirements_from_policy(profile_id="PROFILE-412167F2937C")
    strong_report = matching.match_requirements(doc, tags.extract_resume_tags(STRONG_FIELDS), STRONG_FIELDS)
    weak_report = matching.match_requirements(doc, tags.extract_resume_tags(WEAK_FIELDS), WEAK_FIELDS)
    assert strong_report["overall_coverage"] > weak_report["overall_coverage"] + 0.3
    assert strong_report["label"] in ("高匹配", "较高匹配")
    assert weak_report["label"] in ("低匹配", "偏低匹配")
    assert matching.match_rank_key(strong_report) > matching.match_rank_key(weak_report)
    assert strong_report["disclaimer"] == matching.DISCLAIMER


def test_match_report_excludes_interview_pending_from_denominator():
    doc = requirements.requirements_from_policy(profile_id="PROFILE-412167F2937C")
    report = matching.match_requirements(doc, tags.extract_resume_tags(STRONG_FIELDS), STRONG_FIELDS)
    capability = report["dimensions"]["capability"]
    # 能力维度 6 条里 2 条可由简历证实（规范操作、问题分析），其余是面试核实项
    assert capability["evaluable_count"] == 2
    assert len(capability["interview_pending"]) == 4
    assert capability["coverage_percent"] == 100.0
    assert report["evaluable_count"] + report["interview_pending_count"] == report["requirement_count"]


def test_match_report_hard_filter_hints():
    doc = requirements.requirements_from_policy(profile_id="PROFILE-412167F2937C")
    report = matching.match_requirements(doc, tags.extract_resume_tags(STRONG_FIELDS), STRONG_FIELDS)
    hints = {hint["filter"]: hint for hint in report["hard_filter_hints"]}
    assert hints["education"]["status"] == "满足"
    assert hints["certification"]["status"] == "满足"
    assert hints["location"]["status"] == "满足"

    jd_doc = requirements.requirements_from_jd_text(JD_TEXT, job_name="检验技术员")
    weak_report = matching.match_requirements(jd_doc, tags.extract_resume_tags(WEAK_FIELDS), WEAK_FIELDS)
    weak_hints = {hint["filter"]: hint for hint in weak_report["hard_filter_hints"]}
    assert weak_hints["education"]["status"] == "待核实"  # 大专达标但专业不符 → 需核实


def test_render_matching_markdown_has_evidence_and_disclaimer():
    doc = requirements.requirements_from_policy(profile_id="PROFILE-412167F2937C")
    tag_doc = tags.extract_resume_tags(STRONG_FIELDS)
    report = matching.match_requirements(doc, tag_doc, STRONG_FIELDS)
    markdown = matching.render_matching_markdown(report, resume_summary=tag_doc["summary"])
    assert "综合匹配度" in markdown
    assert "可评估/总数" in markdown
    assert "证据：" in markdown
    assert "待面试核实" in markdown
    assert matching.DISCLAIMER in markdown


def test_textutil_normalizes_ocr_spacing():
    spaced = "姓 名 ： 张 晓 明   学 历 ： 本 科"
    assert textutil.normalize_text(spaced) == "姓名：张晓明 学历：本科"
    assert textutil.find_keywords("室内质控与结果审核", ["室内质控", "不存在的词"]) == ["室内质控"]
    snippet = textutil.evidence_snippet("负责临检标本接收、上机检测与室内质控", "室内质控", radius=6)
    assert "室内质控" in snippet
