"""端到端测试：简历文本 → ingest/字段 → 三维标签 → 要求 → 匹配报告 → 落盘。

只用文本输入（不触发 OCR），真实 PDF/DOCX/图片链路由 tests/test_resume_screening_ingest.py 覆盖。
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from rn.resume_screening import service
from rn.resume_screening.analysis import matching

STRONG_TEXT = """张晓明
电话：13812345678  邮箱：zhangxm@example.com
性别：男  年龄：27  现居地：杭州市西湖区
学历：本科  专业：医学检验技术  毕业院校：浙江中医药大学  毕业年份：2021
工作年限：3年  期望城市：杭州  期望薪资：10-12K
求职意向：检验技术员
证书：检验师资格证
技能：PCR、生化分析仪、室内质控

工作经历
2021.07-2024.08  杭州迪安医学检验所  检验技术员
负责临检标本接收、上机检测、室内质控与结果审核，带教2名实习生，体检季日样本量800+。

自我评价
工作细心严谨，能承受体检季高强度加班，主动复盘异常案例，责任心强。
"""

WEAK_TEXT = """李子涵
电话：13900001111
学历：大专  专业：文秘  现居地：杭州市余杭区
工作年限：0年
技能：Office
自我评价：性格开朗，乐于沟通。
"""

JD_TEXT = """岗位职责：
1. 负责检验标本的接收、上机检测、结果审核与报告发布，执行室内质控与室间质评；
2. 负责试剂领用、库存核对、效期管理；
3. 参与新员工带教与流程优化。

任职要求：
1. 大专及以上学历，医学检验技术/临床医学/生物技术等相关专业；
2. 1年以上医院检验科或第三方医学检验所检验工作经历；
3. 持有检验士/检验师资格证；
4. 熟悉生化分析仪、PCR等仪器的操作；
5. 细心严谨、责任心强，能承受高峰期工作压力。
"""


def test_analyze_text_report_structure():
    report = service.analyze_text(STRONG_TEXT, filename="zhangxm.txt", job_text=JD_TEXT)
    assert report["schema_version"] == service.SCHEMA_VERSION
    assert report["disclaimer"] == matching.DISCLAIMER
    assert report["document"]["text_chars"] > 100
    assert report["fields"]["name"] == "张晓明"
    assert report["fields"]["phone"] == "13812345678"
    assert report["fields"]["education"]["level"] == "本科"
    assert report["requirements"]["source"] == "jd_text"
    assert report["matching"]["overall_percent"] > 50.0
    assert report["matching"]["label"] in ("高匹配", "较高匹配")
    assert report["summary"]["name"] == "张晓明"


def test_analyze_text_prefers_strong_resume():
    strong = service.analyze_text(STRONG_TEXT, filename="strong.txt", job_text=JD_TEXT)
    weak = service.analyze_text(WEAK_TEXT, filename="weak.txt", job_text=JD_TEXT)
    assert strong["matching"]["overall_percent"] > weak["matching"]["overall_percent"] + 25
    assert matching.match_rank_key(strong["matching"]) > matching.match_rank_key(weak["matching"])


def test_analyze_text_with_policy_job():
    report = service.analyze_text(STRONG_TEXT, filename="strong.txt", job="检验技术员", profile_id="PROFILE-412167F2937C")
    assert report["requirements"]["source"] == "policy"
    assert report["matching"]["overall_percent"] > 50.0
    assert report["requirements"]["dimension_counts"] == {"capability": 6, "experience": 3, "trait": 4}


def test_render_and_write_report():
    report = service.analyze_text(STRONG_TEXT, filename="strong.txt", job_text=JD_TEXT)
    markdown = service.render_report_markdown(report)
    assert "# 简历解析与匹配" in markdown
    assert "## 解析字段" in markdown
    assert "## 简历三维标签" in markdown
    assert "综合匹配度" in markdown
    assert matching.DISCLAIMER in markdown

    workdir = Path(tempfile.mkdtemp(prefix="rn-service-"))
    try:
        written = service.write_report(report, out_dir=workdir / "out", stem="analysis")
        payload = json.loads(Path(written["json"]).read_text(encoding="utf-8"))
        assert payload["fields"]["name"] == "张晓明"
        assert "综合匹配度" in Path(written["markdown"]).read_text(encoding="utf-8")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def test_analyze_resume_files_ranks_and_collects_errors():
    workdir = Path(tempfile.mkdtemp(prefix="rn-batch-"))
    try:
        (workdir / "strong.txt").write_text(STRONG_TEXT, encoding="utf-8")
        (workdir / "weak.txt").write_text(WEAK_TEXT, encoding="utf-8")
        missing = workdir / "missing.pdf"
        batch = service.analyze_resume_files(
            [workdir / "strong.txt", workdir / "weak.txt", missing],
            job_text=JD_TEXT,
        )
        assert len(batch["reports"]) == 2
        assert len(batch["errors"]) == 1
        assert "missing.pdf" in batch["errors"][0]["path"]
        ranking = batch["ranking"]
        assert ranking[0]["filename"] == "strong.txt"
        assert ranking[0]["rank"] == 1
        assert ranking[0]["overall_percent"] > ranking[1]["overall_percent"]
        # 排序行先给「命中了几项」，带出待验证项清单，反面证据单列
        assert ranking[0]["tag_hit_count"] is not None
        assert isinstance(ranking[0]["pending_verification"], list)
        assert ranking[0]["tag_contradicted_count"] == 0

        written = service.write_batch_report(batch, out_dir=workdir / "out")
        assert Path(written["json"]).exists()
        batch_md = Path(written["markdown"]).read_text(encoding="utf-8")
        assert "## 排序总览" in batch_md
        assert "命中标签" in batch_md
        assert "待验证项" in batch_md
        assert "未命中（反证）" in batch_md
        assert "## 失败文件" in batch_md
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def test_analyze_resume_bytes_text_upload():
    report = service.analyze_resume_bytes(
        STRONG_TEXT.encode("utf-8"), filename="upload.txt", media_type="text/plain", job_text=JD_TEXT,
    )
    assert report["fields"]["name"] == "张晓明"
    assert report["matching"]["overall_percent"] > 50.0


def test_analyze_resume_files_cache_reuses_previous_result():
    """同一份简历 + 同一份 JD 重跑时走缓存，批处理/断点续跑不用重新 OCR。"""
    workdir = Path(tempfile.mkdtemp(prefix="rn-cache-"))
    try:
        (workdir / "strong.txt").write_text(STRONG_TEXT, encoding="utf-8")
        cache_dir = workdir / "cache"
        first = service.analyze_resume_files(
            [workdir / "strong.txt"], job_text=JD_TEXT, cache_dir=cache_dir,
        )
        assert first["cache"] == {"enabled": True, "hits": 0, "misses": 1}
        second = service.analyze_resume_files(
            [workdir / "strong.txt"], job_text=JD_TEXT, cache_dir=cache_dir,
        )
        assert second["cache"] == {"enabled": True, "hits": 1, "misses": 0}
        assert second["ranking"][0]["overall_percent"] == first["ranking"][0]["overall_percent"]
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
