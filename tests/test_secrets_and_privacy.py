import inspect
import json

from rn.job_contract_v3 import normalize_resume_facts
from rn.resume_mysql import ResumeMySQLBackend


def test_mysql_backend_has_no_hardcoded_password_default(monkeypatch):
    monkeypatch.delenv("MYSQL_PASSWORD", raising=False)
    backend = ResumeMySQLBackend()
    assert backend.password == ""
    source = inspect.getsource(ResumeMySQLBackend.__init__)
    assert "dazd@" not in source


def test_resume_facts_redact_common_sensitive_labels_and_contacts():
    facts = normalize_resume_facts({
        "resume_id": "C1", "name": "A", "education": {"level": "本科"},
        "raw_text": "性别：男\n年龄：35岁\n电话 13800138000\n邮箱 a@example.com\n负责高可用平台。",
        "work_experiences": [],
    })
    text = facts["raw_text"]
    assert "男" not in text
    assert "35岁" not in text
    assert "13800138000" not in text
    assert "a@example.com" not in text
    assert "负责高可用平台" in text


def test_mysql_hometown_never_masquerades_as_current_location():
    from rn.resume_mysql import ResumeMySQLBackend
    backend = ResumeMySQLBackend(password="test-only", database="test")
    row = {
        "id": 1,
        "name": "候选人",
        "phone": "",
        "hometown_address": "北京",
        "expect_job_objs": '[{"city":"杭州"}]',
        "job_exp_objs": "[]",
        "cert_objs": "[]",
        "skills_objs": "[]",
        "tags": "{}",
        "education_objs": "[]",
        "proj_exp_objs": "[]",
    }
    resume = backend._resume_row_to_dict(row)
    assert resume["location"] == ""
    assert resume["expected_location"] == "杭州"
    assert "hometown_address" not in resume


def test_mysql_imported_normalized_resume_is_hydrated_instead_of_using_filename_as_raw_text():
    backend = ResumeMySQLBackend(password="test-only", database="test")
    backend._get_info_by_phone = lambda _phone: None
    row = {
        "id": 88, "name": "", "phone": "", "resume_name": "候选人_检验技术员.pdf",
        "raw_text": "候选人甲\n本科\n医学检验技术\n某医院检验科 2021-2024",
        "raw_json": json.dumps({"normalized_resume": {
            "resume_id": "R-imported", "name": "候选人甲",
            "education": {"level": "本科", "major": "医学检验技术"},
            "work_experiences": [{"company": "某医院", "position": "检验技术员", "description": "临床检验"}],
            "certifications": ["检验师资格证"], "skills": ["生化检测"],
            "raw_text": "候选人甲\n本科\n医学检验技术\n某医院检验科 2021-2024",
        }}, ensure_ascii=False),
        "job_exp_objs": "[]", "cert_objs": "[]", "skills_objs": "[]", "tags": "{}",
        "education_objs": "[]", "proj_exp_objs": "[]", "expect_job_objs": "[]",
        "source_file_path": "/controlled/resume.pdf", "fetched_by": "import",
    }

    resume = backend._resume_row_to_dict(row)

    assert resume["name"] == "候选人甲"
    assert resume["education"]["level"] == "本科"
    assert resume["work_experiences"][0]["company"] == "某医院"
    assert resume["certifications"] == ["检验师资格证"]
    assert resume["raw_text"].startswith("候选人甲")


def test_mysql_uses_stored_resume_body_when_legacy_fields_only_supply_a_filename():
    backend = ResumeMySQLBackend(password="test-only", database="test")
    backend._get_info_by_phone = lambda _phone: None
    row = {
        "id": 89, "name": "候选人乙", "phone": "", "degree": "本科",
        "resume_name": "候选人乙_检验技术员.pdf",
        "raw_text": "候选人乙\n本科医学检验技术\n2021-2024 某医院检验科负责生化检测与质控。",
        "raw_json": json.dumps({"result": {"source": "mineru"}}, ensure_ascii=False),
        "job_exp_objs": "[]", "cert_objs": "[]", "skills_objs": "[]", "tags": "{}",
        "education_objs": "[]", "proj_exp_objs": "[]", "expect_job_objs": "[]",
    }

    resume = backend._resume_row_to_dict(row)

    assert resume["raw_text"].startswith("候选人乙\n本科医学检验技术")
