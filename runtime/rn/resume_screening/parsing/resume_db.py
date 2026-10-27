"""简历库 (Resume DB) - 预留接口 + 本地 JSON 后端。

设计目标（来自旧版 Recruiter-Na 数据契约）：
- 简历从邮件附件自动入库（mail intake），也可人工/文件夹导入；
- 入库只保存简历事实与来源，不生成候选人数值评分；
- 检索只负责召回候选人；通过/不通过由 v5 Candidate Decision 链路决定；
- 后续可无缝替换为 ATS / MySQL / 云 API 后端（实现同样的接口即可）。

预留的后端适配点：
  ResumeStore(backend="http") -> ResumeHttpBackend(url=RESUME_DB_API_URL)
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

RESUME_SCHEMA_KEYS = (
    "resume_id", "name", "gender", "education", "location",
    "total_work_years", "industry_tags", "certifications", "skills",
    "expected_salary", "current_company", "current_position",
    "work_experiences", "projects", "raw_text",
    "source", "created_at", "sha256", "status", "routing",
)
STATUS_NEW = "NEW"
STATUS_REVIEW_REQUIRED = "REVIEW_REQUIRED"
STATUS_PARSED = "PARSED"


class ResumeBackend:
    """简历库后端接口：云部署时把这里换成语义一致的 ATS / MySQL / 云 API 实现。"""

    def search(self, query: str = "", **filters) -> list[dict[str, Any]]:
        raise NotImplementedError

    def get(self, resume_id: str) -> Optional[dict[str, Any]]:
        raise NotImplementedError

    def upsert(self, resume: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def stats(self) -> dict[str, Any]:
        raise NotImplementedError


@dataclass
class JsonResumeStore(ResumeBackend):
    """本地测试/自托管版：单个 JSON 文件当作“简历数据库”，带写锁。"""

    path: Path
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({"version": 1, "resumes": []})

    def _read(self) -> dict[str, Any]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, doc: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def upsert(self, resume: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            doc = self._read()
            resumes = doc["resumes"]
            idx = next((i for i, r in enumerate(resumes) if r.get("resume_id") == resume.get("resume_id")), None)
            if idx is None:
                resumes.append(resume)
            else:
                # 保留已有不可变审计字段
                old = resumes[idx]
                resume.setdefault("sha256", old.get("sha256"))
                resume.setdefault("created_at", old.get("created_at"))
                resumes[idx] = resume
            doc["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            self._write(doc)
            return resume

    def get(self, resume_id: str) -> Optional[dict[str, Any]]:
        doc = self._read()
        return next((r for r in doc["resumes"] if r.get("resume_id") == resume_id), None)

    def search(self, query: str = "", **filters: Any) -> list[dict[str, Any]]:
        """按关键词 + 结构化过滤。filters: degree (最低学历), years_min,
        location (包含), tags (技能/行业标签全部命中), certs (证书全部命中),
        status, source, limit。"""
        query = (query or "").strip().lower()
        qwords = [w for w in re.split(r"[\s,，、;；]+", query) if w]
        degree_min = filters.get("degree")
        years_min = filters.get("years_min")
        location = filters.get("location")
        tags = filters.get("tags") or []
        certs = filters.get("certs") or []
        status = filters.get("status")
        source = filters.get("source")
        limit = int(filters.get("limit") or 50)
        out = []
        with self._lock:
            doc = self._read()
            for r in doc["resumes"]:
                if status and r.get("status") != status:
                    continue
                if source and r.get("source") != source:
                    continue
                if degree_min and _degree_rank(r.get("education", {}).get("level", "")) < _degree_rank(degree_min):
                    continue
                if years_min is not None and int(r.get("total_work_years") or 0) < int(years_min):
                    continue
                if location and location not in (r.get("location") or ""):
                    continue
                if tags:
                    hay = " ".join(r.get("skills") or []) + " " + " ".join(r.get("industry_tags") or []) + " " + (r.get("raw_text") or "")
                    hay = hay.lower()
                    if not all(str(tag).lower() in hay for tag in tags):
                        continue
                if certs:
                    have = {c.lower() for c in (r.get("certifications") or [])}
                    if not all(c.lower() in have for c in certs):
                        continue
                if qwords:
                    hay = (json.dumps(r, ensure_ascii=False) + " " + " ".join([
                        str(r.get("name", "")), str(r.get("current_position", "")),
                        " ".join(r.get("skills") or []), " ".join(r.get("industry_tags") or []),
                        " ".join(r.get("certifications") or []), str(r.get("raw_text") or "")])).lower()
                    if not all(w in hay for w in qwords):
                        continue
                out.append(r)
        # v5 retrieval does not rank by legacy RQ. Preserve source/insertion order
        # so candidate ordering is decided only after Candidate Decision.
        return out[:limit]

    def all_resumes(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._read()["resumes"])

    def stats(self) -> dict[str, Any]:
        with self._lock:
            resumes = self._read()["resumes"]
        statuses: dict[str, int] = {}
        for r in resumes:
            statuses[r.get("status", "?")] = statuses.get(r.get("status", "?"), 0) + 1
        return {"total": len(resumes), "by_status": statuses}


class ResumeHttpBackend(ResumeBackend):
    """云部署预留：对接 ATS / 云简历库 API（HTTP JSON）。

    接口约定（与 JsonResumeStore 对齐）：
      GET  /resumes?q=&degree=&years_min=&location=&tags=&limit=
      GET  /resumes/{id}
      PUT  /resumes/{id}
      GET  /resumes/stats
    """

    def __init__(self, base_url: str, token_env: str = "RESUME_DB_API_TOKEN"):
        self.base_url = base_url.rstrip("/")
        self.token_env = token_env

    def _headers(self) -> dict[str, str]:
        import os
        h = {"Content-Type": "application/json"}
        token = os.environ.get(self.token_env, "")
        if token:
            h["Authorization"] = f"Bearer {token}"
        return h

    def search(self, query: str = "", **filters) -> list[dict[str, Any]]:
        import urllib.parse, urllib.request
        params = {"q": query}
        for k in ("degree", "years_min", "location", "tags", "certs", "status", "source", "limit"):
            if filters.get(k) is not None:
                params[k] = filters[k]
        if isinstance(params.get("tags"), list):
            params["tags"] = ",".join(params["tags"])
        if isinstance(params.get("certs"), list):
            params["certs"] = ",".join(params["certs"])
        url = self.base_url + "/resumes?" + urllib.parse.urlencode({k: v for k, v in params.items() if v != ""})
        req = urllib.request.Request(url, headers=self._headers())
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def get(self, resume_id: str) -> Optional[dict[str, Any]]:
        import urllib.request
        req = urllib.request.Request(f"{self.base_url}/resumes/{resume_id}", headers=self._headers())
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def upsert(self, resume: dict[str, Any]) -> dict[str, Any]:
        import urllib.request
        rid = resume["resume_id"]
        data = json.dumps(resume, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(f"{self.base_url}/resumes/{rid}", data=data, headers=self._headers(), method="PUT")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def stats(self) -> dict[str, Any]:
        import urllib.request
        req = urllib.request.Request(self.base_url + "/resumes/stats", headers=self._headers())
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))


def _degree_rank(level: str) -> int:
    if not level:
        return 0
    level = str(level).strip().lower()
    table = {"高中": 1, "中专": 1, "中技": 1, "职高": 1, "大专": 2, "专科": 2,
             "本科": 3, "学士": 3, "bachelor": 3, "硕士": 4, "研究生": 4, "master": 4, "博士": 5, "doctor": 5}
    for key, rank in table.items():
        if key in level:
            return rank
    return 0


def sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_of_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_text_resume(text: str, resume_id: str | None = None, source: str = "upload",
                      created_at: str | None = None, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """极简文本简历解析器：抽取 KV 行 + 段落转 raw_text。

    真实环境应替换为 Recruiter-Na 的完整解析（PDF/DOCX + LLM 结构化抽取），
    本函数保证本地 dry-run 与测试可用。解析出的字段一律视为“待核实的证据”，
    这些结构化字段 + raw_text 只作为 v5 Decision Evidence Evaluator 的候选人事实输入；
    最终 PASS / VERIFY / FAIL 必须在证据落地校验后由确定性 Candidate Decision 规则产生。
    """
    meta = meta or {}
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    def kv(key: str) -> str:
        rx = re.compile(rf"^\s*{key}\s*[:：]\s*(.+)$", re.IGNORECASE)
        for ln in lines:
            m = rx.match(ln)
            if m:
                return m.group(1).strip()
        return ""
    education = {}
    for key in ("学历", "学位"):
        v = kv(key)
        if v:
            education["level"] = v
            break
    school = kv("毕业院校") or kv("学校")
    major = kv("专业")
    if school:
        education["school"] = school
    if major:
        education["major"] = major
    exp_block = _block_after(lines, ("工作经历", "工作经验", "职业经历"))
    work_experiences = _parse_experiences(exp_block)
    if not work_experiences:
        # 退而求其次：任意包含年份区间的行
        for ln in lines:
            m = re.match(r"^(.{0,40}?)(20\d{2})\.?\s?[年/-]?(\d{1,2})?(?:[月/-].*)?\s?-\s?(20\d{2}|至今)", ln)
            if m:
                work_experiences.append({"company": m.group(1).strip(), "position": "", "start_date": m.group(2), "end_date": m.group(3) or "至今", "description": ""})
    years = kv("工作年限") or kv("经验年限")
    try:
        total_work_years = int(float(re.sub(r"[^0-9.]", "", years)))
    except Exception:
        total_work_years = max([0] + [e.get("_months", 0) for e in work_experiences]) // 12 if work_experiences else 0
    industry_tags = [t.strip() for t in re.split(r"[，,、;；]", kv("行业")) if t.strip()]
    certs = [t.strip() for t in re.split(r"[，,、;；]", kv("证书") or kv("资质")) if t.strip()]
    skills = [t.strip() for t in re.split(r"[，,、;；]", kv("技能")) if t.strip()]
    name = kv("姓名")
    if not name:
        # 从 "XX 求职意向" 或文件名的前几个字提取
        for ln in lines:
            m = re.match(r"^([一-鿿]{2,4})\s+(求职意向|应聘职位)", ln)
            if m:
                name = m.group(1)
                break
        if not name and meta.get("filename"):
            fn = meta["filename"]
            stem = fn.rsplit(".", 1)[0]
            for junk in ("\u221a", "【", "】", "个人简历-", "简历-", "简历", "猎聘简历", "候选人-"):
                stem = stem.replace(junk, "")
            import re as _re
            # Exported recruiter files commonly use ``姓名_岗位``. Treat that
            # standalone prefix as a source label, never a title-derived name.
            parts = _re.split(r"[\s_\-\u2014]+", stem)
            for p in parts:
                p = p.strip()
                if _re.match(r"^[\u4e00-\u9fff\u3400-\u4dbf]{2,4}$", p) and not any(
                    kw in p for kw in ("简历","个人","医学","经理","专员","工程师","猎聘","经验","学历","博士","简历","职称","候选","年龄")):
                    name = p
                    break
    location = kv("所在地") or kv("地点")
    if not location:
        for ln in lines:
            m = re.search(r"(?:籍贯|所在地|地点|现居)[：:]\s*([一-鿿]{2,4}(?:省|市|区)?)", ln)
            if m:
                location = m.group(1)
                break
    salary = kv("期望薪资") or kv("薪资")
    position = kv("职位") or kv("当前职位") or (work_experiences[0].get("position") if work_experiences else "")
    # 从求职意向行提取
    if not position:
        for ln in lines:
            m = re.search(r"求职意向[：:]\s*([一-鿿/、，,]+)", ln)
            if m:
                position = m.group(1).strip("，,/")
                break
    company = kv("公司") or (work_experiences[0].get("company") if work_experiences else "")
    # 从教育背景/个人背景段落补充 education
    if not education.get("level") or not education.get("school"):
        edu_section = _block_after(lines, ("个人背景", "教育背景", "教育培训", "教育"))
        edu_lines = _parse_education_lines(edu_section)
        if edu_lines:
            e = edu_lines[0]
            if not education.get("level") and e.get("level"):
                education["level"] = e["level"]
            if not education.get("school") and e.get("school"):
                education["school"] = e["school"]
            if not education.get("major") and e.get("major"):
                education["major"] = e["major"]
    rid = resume_id or f"R-{sha256_of_text(text)[:12]}"
    resume = {
        "resume_id": rid,
        "name": name or "未命名",
        "gender": kv("性别"),
        "education": education,
        "location": location,
        "total_work_years": total_work_years,
        "industry_tags": industry_tags or ["医疗健康"],
        "certifications": certs,
        "skills": skills,
        "expected_salary": salary,
        "current_company": company,
        "current_position": position,
        "work_experiences": work_experiences,
        "projects": [],
        "raw_text": text,
        "source": source,
        "created_at": created_at or time.strftime("%Y-%m-%dT%H:%M:%S"),
        "sha256": sha256_of_text(text),
        "status": (meta.get("routing") and meta["routing"].get("listing_id")) and STATUS_NEW or STATUS_REVIEW_REQUIRED,
        "routing": meta.get("routing") or {},
    }
    return resume


DEGREE_KEYWORDS = {
    "博士": "博士", "doctor": "博士",
    "硕士": "硕士", "master": "硕士", "研究生": "硕士",
    "本科": "本科", "学士": "本科", "bachelor": "本科",
    "大专": "大专", "专科": "大专",
    "高中": "高中", "中专": "中专",
}


def _parse_education_lines(lines: list[str]) -> list[dict[str, str]]:
    """从背景/教育段落解析学历、学校、专业。

    行格式如：
      2019.09—2022.06 浙江师范大学 生命科学学院 生物学/遗传学 硕士
    """
    out = []
    for ln in lines:
        m = re.match(
            r"^(?:\d{4})[-/.]?\d{0,2}?\s*[-—–~至到]+\s*(?:\d{4}|至今)[-/.]?\d{0,2}?\s*(.*)$", ln)
        if not m:
            continue
        rest = m.group(1)
        if not rest:
            continue
        parts = rest.split()
        if not parts:
            continue
        level = ""
        for kw, lv in sorted(DEGREE_KEYWORDS.items(), key=lambda x: -len(x[0])):
            if kw in rest:
                level = lv
                break
        school = ""
        major = ""
        # 学校通常在第一部分（不含常见专业词）；专业在中间或最后带斜杠
        for p in parts:
            if "大学" in p or "学院" in p:
                school = p
            elif "/" in p or "、" in p:
                major = p
        if not school and parts:
            school = parts[0]
        if not major and len(parts) > 1:
            major = parts[-2] if parts[-1] in ("本科", "硕士", "博士", "大专", "研究生") else parts[-1]
        out.append({"level": level, "school": school, "major": major})
    return out


def _block_after(lines: list[str], headers: Iterable[str]) -> list[str]:
    start = -1
    for i, ln in enumerate(lines):
        if any(h in ln for h in headers):
            start = i + 1
            break
    if start < 0:
        return []
    out = []
    for ln in lines[start:]:
        if any(h in ln for h in ("教育背景", "教育培训", "自我评价", "项目经历", "期望薪资", "求职意向")):
            break
        out.append(ln)
    return out


def _parse_experiences(lines: list[str]) -> list[dict[str, Any]]:
    """解析“公司 (YYYY[-MM] 至 YYYY[-MM]|至今) 职位”行，后续非年份行并入职责描述。

    支持格式示例：
      某医院 (2014-07 至 2018-02) 住院医师
      某三甲医院 (2018-03 至 至今) 病理医生
    """
    out: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        m = re.match(
            r"^(.*?)[（(]?(\d{4})[-/.]年?(\d{1,2})?\s*[-—–~至到]+\s*(\d{4})[-/.]年?(\d{1,2})?[)）]?\s*(.*)$", ln)
        if not m:
            m = re.match(
                r"^(.*?)[（(]?(\d{4})[-/.]年?(\d{1,2})?\s*[-—–~至到]+\s*至今[)）]?\s*(.*)$", ln)
            if not m:
                i += 1
                continue
            company = m.group(1).strip(" |，,、")
            start = "%s-%02d" % (m.group(2), int(m.group(3))) if m.group(3) else m.group(2)
            end = "至今"
            rest = (m.group(4) or "").strip()
        else:
            company = m.group(1).strip(" |，,、")
            start = "%s-%02d" % (m.group(2), int(m.group(3))) if m.group(3) else m.group(2)
            end = "%s-%02d" % (m.group(4), int(m.group(5))) if m.group(5) else m.group(4)
            rest = (m.group(6) or "").strip()
        # 从 rest 提取公司/职位：rest 格式为 "公司 部门 职位"
        if not company and rest:
            parts = rest.split(None, 1)
            company = parts[0]
            rest = parts[1] if len(parts) > 1 else ""
        if rest:
            rest_parts = rest.split()
            if rest_parts:
                # 职位通常是最后一个词（或带斜杠组合）
                position = rest_parts[-1]
            else:
                position = ""
        else:
            position = ""
        desc_lines = []
        i += 1
        while i < len(lines) and not re.match(r"^(?:.{0,14}?)[（(]?\d{4}", lines[i]):
            desc_lines.append(lines[i].strip())
            i += 1
        out.append({"company": company, "position": position,
                    "start_date": start, "end_date": end,
                    "description": " ".join(desc_lines)})
    return out
