import inspect

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
