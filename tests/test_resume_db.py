"""简历库 + 邮件入库测试：去重、受控路由、搜索过滤。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))

from rn.resume_db import JsonResumeStore, parse_text_resume, sha256_of_text  # noqa: E402
from rn.mail_watch import MailIntake  # noqa: E402


RESUME_A = """
姓名：王明
学历：本科
毕业院校：某医科大学
专业：临床医学
所在地：杭州
工作年限：8年
行业：医疗健康
证书：执业医师资格证
技能：病理诊断，免疫组化，FISH
当前职位：病理医生
期望薪资：25k
工作经历
某三甲医院 (2018-03 至 至今) 病理医生
负责常规病理诊断与术中冰冻，年诊断量15000例，主导科室质控改进。
某医院 (2014-07 至 2018-02) 住院医师
病理科轮转，完成规培。
"""


def test_ingest_dedupe(tmp_path):
    store = JsonResumeStore(tmp_path / "db.json")
    a = parse_text_resume(RESUME_A, source="email")
    store.upsert(a)
    dup = parse_text_resume(RESUME_A, source="email")
    store.upsert(dup)
    assert store.stats()["total"] == 1


def test_parse_text_resume_uses_a_filename_prefix_as_candidate_name():
    resume = parse_text_resume("医学检验技术\n检验科工作经历", meta={"filename": "李静文_驻点检验技术员.pdf"})

    assert resume["name"] == "李静文"


def test_review_required_without_routing(tmp_path):
    store = JsonResumeStore(tmp_path / "db.json")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    f = inbox / "resume.txt"
    f.write_text(RESUME_A, encoding="utf-8")
    intake = MailIntake(store=store, inbox_dir=inbox)
    rows = intake.watch_local()
    assert len(rows) == 1
    assert rows[0]["status"] == "REVIEW_REQUIRED"
    # 再次入库去重
    assert intake.watch_local() == []


def test_controlled_routing(tmp_path):
    store = JsonResumeStore(tmp_path / "db.json")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    f = inbox / "resume.txt"
    f.write_text(RESUME_A, encoding="utf-8")
    (inbox / "resume.txt.meta.json").write_text(
        '{"routing": {"listing_id": "J-2026-001"}}', encoding="utf-8")
    intake = MailIntake(store=store, inbox_dir=inbox)
    rows = intake.watch_local()
    assert rows[0]["status"] == "NEW"
    assert rows[0]["routing"]["job_id"] == "J-2026-001"


def test_search_filters(tmp_path):
    store = JsonResumeStore(tmp_path / "db.json")
    a = parse_text_resume(RESUME_A, source="email")
    store.upsert(a)
    b = parse_text_resume(RESUME_A.replace("病理医生", "护士").replace("病理诊断", "护理")
                    .replace("病理科轮转", "内科轮转").replace("病理相关", "护理相关"), source="email")
    b["resume_id"] = "R-b"
    b["sha256"] = sha256_of_text(b["raw_text"])
    store.upsert(b)
    rows = store.search("病理", degree="本科")
    assert len(rows) == 1
    rows = store.search("", tags=["病理诊断"])
    assert len(rows) == 1
    rows = store.search("", years_min=10)
    assert rows == []
