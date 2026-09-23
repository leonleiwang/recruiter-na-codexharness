"""MySQL 简历库后端 —— 对接实习生邮件同步项目数据库。

全量映射 candidate_resume（阿里云解析结果）+ candidate_info（衍生字段）
+ mail_receive_log（邮件元数据）三张表到招聘娜标准 resume dict，
确保业务同事提出的所有评分字段都被有效使用。

使用方式：
  rn.cli --backend mysql [status|search|match|ingest...]
"""
from __future__ import annotations

import json
import os
import time
import hashlib
from pathlib import Path
from typing import Any, Optional

from rn.resume_db import ResumeBackend, STATUS_NEW, sha256_of_text

try:
    import pymysql
    PYMYSQL_AVAILABLE = True
except ImportError:
    PYMYSQL_AVAILABLE = False


class ResumeMySQLBackend(ResumeBackend):
    """从 MySQL (candidate_resume + candidate_info + mail_receive_log) 读取简历。
    
    Connection defaults come only from environment variables; no credentials
    are embedded in source or release artifacts.

    字段映射关系见 _resume_row_to_dict 方法。
    """

    def __init__(self, host=None, port=None, user=None, password=None,
                 database=None, table_resume="candidate_resume",
                 table_info="candidate_info", table_log="mail_receive_log"):
        self.host = host if host is not None else os.environ.get("MYSQL_HOST", "127.0.0.1")
        self.port = int(port if port is not None else os.environ.get("MYSQL_PORT", "3306"))
        self.user = user if user is not None else os.environ.get("MYSQL_USER", "root")
        self.password = password if password is not None else os.environ.get("MYSQL_PASSWORD", "")
        self.database = database if database is not None else os.environ.get("MYSQL_DATABASE", "")
        self.table_resume = table_resume
        self.table_info = table_info
        self.table_log = table_log
        self._conn = None

    def _connect(self):
        if self._conn is not None and self._conn.open:
            try:
                self._conn.ping(reconnect=False)
                return self._conn
            except Exception:
                self._conn.close()
                self._conn = None
        if not PYMYSQL_AVAILABLE:
            raise ImportError("需要安装 pymysql：pip install pymysql")
        if not self.database:
            raise ValueError("MYSQL_DATABASE is required")
        if not self.password:
            raise ValueError("MYSQL_PASSWORD is required")
        self._conn = pymysql.connect(
            host=self.host, port=self.port, user=self.user,
            password=self.password, database=self.database,
            charset="utf8mb4", connect_timeout=15,
            cursorclass=pymysql.cursors.DictCursor,
        )
        return self._conn

    def _resume_row_to_dict(self, row: dict) -> dict:
        """将 candidate_resume 行 + 关联的 candidate_info + mail_receive_log
        转换为招聘娜标准 resume dict，全量字段映射。
        
        ===== 评分引擎直接使用的字段 =====
        resume_id, name, gender, education, location,
        total_work_years, industry_tags, certifications, skills,
        expected_salary, current_company, current_position,
        work_experiences, projects, raw_text,
        rq (简历质量分，由引擎自行计算)
        
        ===== 业务同事要求的额外评分数据(保留在 _mysql_ 前缀) =====
        _mysql_work_year_inf: 工作年限文本描述
        _mysql_work_start_time: 最早工作开始时间
        _mysql_work_pos_type_p: 岗位类型
        _mysql_college_rank: 院校排名
        _mysql_college_type: 院校类型(985/211等)
        _mysql_resume_integrity: 简历完整度
        _mysql_job_stability: 工作稳定度评分
        _mysql_risks: 风险标签(JSON)
        _mysql_highlights: 简历亮点(JSON)
        _mysql_eval: AI评估(JSON)
        _mysql_profiler: 画像分析(JSON)
        _mysql_capacity: 能力分析(JSON)
        _mysql_stability: 稳定性分析(JSON)
        _mysql_predicted_pos_types: 预测岗位类型
        _mysql_predicted_industries: 预测行业
        _mysql_expect_job_objs: 期望工作信息(含薪资/城市)
        _mysql_proj_exp_objs: 项目经验(JSON)
        _mysql_training_objs: 培训经历(JSON)
        _mysql_social_exp_objs: 社会经历(JSON)
        _mysql_lang_objs: 语言能力(JSON)
        _mysql_all_cert_objs: 全部证书(JSON)
        _mysql_cont_job_skill: 工作技能文本
        _mysql_cont_proj_exp: 项目经验文本
        _mysql_raw_json: 阿里云API原始返回
        _mysql_info: candidate_info 衍生字段
        _mysql_mail: mail_receive_log 邮件元数据
        """
        # ===== 基础字段映射 =====
        name = row.get("name") or ""
        phone = row.get("phone") or ""
        email = row.get("email") or ""
        gender = row.get("gender") or ""

        # ===== 教育信息 =====
        education = {}
        degree = row.get("degree") or ""
        if degree:
            education["level"] = degree
        college = row.get("college") or ""
        if college:
            education["school"] = college
        major = row.get("major") or ""
        if major:
            education["major"] = major
        # 补充教育信息
        if row.get("college_type"):
            education["college_type"] = row.get("college_type")
        if row.get("college_rank"):
            education["college_rank"] = row.get("college_rank")
        if row.get("grad_time"):
            education["graduation_date"] = row.get("grad_time")

        # 从 education_objs 补充教育细节
        try:
            edu_objs = json.loads(row.get("education_objs") or "[]")
            if edu_objs and not education.get("school"):
                e = edu_objs[0]
                education["school"] = e.get("school") or e.get("college") or ""
                education["major"] = e.get("major") or ""
                education["level"] = e.get("degree") or ""
        except (json.JSONDecodeError, TypeError):
            pass

        # ===== 工作年限 =====
        total_work_years = 0
        try:
            total_work_years = int(float(row.get("work_year") or 0))
        except (ValueError, TypeError):
            pass

        # ===== 工作经历 =====
        work_experiences = []
        try:
            job_exp_objs = json.loads(row.get("job_exp_objs") or "[]")
            for exp in job_exp_objs:
                work_experiences.append({
                    "company": exp.get("company_name") or exp.get("org_name") or exp.get("job_cpy") or "",
                    "position": exp.get("position") or exp.get("job_title") or exp.get("job_position") or "",
                    "start_date": exp.get("start_time") or exp.get("start_date") or exp.get("start_date") or "",
                    "end_date": exp.get("end_time") or exp.get("end_date") or exp.get("end_date") or "至今",
                    "description": exp.get("description") or exp.get("job_content") or "",
                    "department": exp.get("department") or "",
                    "industry": exp.get("industry") or "",
                })
        except (json.JSONDecodeError, TypeError):
            pass

        # ===== 证书 =====
        certifications = []
        try:
            cert_objs = json.loads(row.get("cert_objs") or "[]")
            for c in cert_objs:
                cert_name = c.get("cert_name") or c.get("name") or ""
                if cert_name:
                    certifications.append(cert_name)
        except (json.JSONDecodeError, TypeError):
            pass

        # ===== 技能 =====
        skills = []
        try:
            skills_objs = json.loads(row.get("skills_objs") or "[]")
            for s in skills_objs:
                skill_name = s.get("skill_name") or s.get("name") or ""
                if skill_name:
                    skills.append(skill_name)
        except (json.JSONDecodeError, TypeError):
            pass

        # ===== 行业标签（从 tags + predicted_industries 提取） =====
        industry_tags = []
        try:
            tags = json.loads(row.get("tags") or "{}")
            if isinstance(tags, dict):
                for k, v in tags.items():
                    if isinstance(v, str) and v:
                        industry_tags.append(v)
        except (json.JSONDecodeError, TypeError):
            pass
        # 补充预测行业
        for pred_field in ("predicted_industries_c1", "predicted_industries_c2"):
            try:
                pred = json.loads(row.get(pred_field) or "[]")
                if isinstance(pred, list):
                    for item in pred:
                        if isinstance(item, str) and item not in industry_tags:
                            industry_tags.append(item)
            except (json.JSONDecodeError, TypeError):
                pass

        # ===== 期望薪资 =====
        salary = row.get("predicted_salary") or ""
        # 从 expect_job_objs 补充期望薪资和城市
        expected_city = ""
        try:
            expect_objs = json.loads(row.get("expect_job_objs") or "[]")
            if expect_objs:
                e = expect_objs[0]
                if not salary:
                    salary = (e.get("salary") or e.get("expect_salary") or e.get("min_salary") or "")
                expected_city = e.get("city") or e.get("expect_city") or ""
        except (json.JSONDecodeError, TypeError):
            pass

        # ===== 当前公司/职位 =====
        current_company = row.get("work_company") or ""
        current_position = row.get("work_position") or ""

        # ===== 地点 =====
        # Never treat hometown/household registration as current work location.
        # Current city may be supplied later by candidate_info; expected city is
        # a separate willingness signal and must never masquerade as current city.
        location = ""

        # ===== 项目经验 =====
        projects = []
        try:
            proj_objs = json.loads(row.get("proj_exp_objs") or "[]")
            for p in proj_objs:
                projects.append({
                    "name": p.get("project_name") or p.get("name") or "",
                    "role": p.get("role") or p.get("position") or "",
                    "description": p.get("description") or "",
                    "start_date": p.get("start_time") or p.get("start_date") or "",
                    "end_date": p.get("end_time") or p.get("end_date") or "",
                })
        except (json.JSONDecodeError, TypeError):
            pass

        # ===== 拼接 raw_text（评分引擎做标签匹配） =====
        legacy_raw_text = " ".join(filter(None, [
            row.get("cont_basic_info") or "",
            row.get("cont_education") or "",
            row.get("cont_job_exp") or "",
            row.get("cont_job_skill") or "",
            row.get("cont_proj_exp") or "",
        ]))
        stored_raw_text = str(row.get("raw_text") or "").strip()
        if stored_raw_text and (not legacy_raw_text.strip() or len(stored_raw_text) >= len(legacy_raw_text.strip())):
            raw_text = stored_raw_text
        elif legacy_raw_text.strip():
            raw_text = legacy_raw_text
        else:
            raw_text = row.get("resume_name") or ""

        # ===== 简历ID =====
        rid = "MYSQL-" + sha256_of_text(name + phone + current_company + current_position)[:12]

        # ===== 从 candidate_info 表读取人工修正后的字段 =====
        info = self._get_info_by_phone(phone)
        if info:
            # 用 candidate_info 修正后的值覆盖阿里云解析的原始值
            if info.get("highest_education"):
                education["level"] = info["highest_education"]
            if info.get("graduation_school"):
                education["school"] = info["graduation_school"]
            if info.get("major"):
                education["major"] = info["major"]
            if info.get("work_years") is not None and info["work_years"] > 0:
                total_work_years = info["work_years"]
            if info.get("current_city"):
                location = info["current_city"]
            if info.get("current_title"):
                current_position = info["current_title"]
            if info.get("current_company"):
                current_company = info["current_company"]
            if info.get("applied_position"):
                current_position = info["applied_position"]  # 应聘岗位优先
            if info.get("risk_notes"):
                pass  # 风险标签保留在 _mysql_info 中
            if info.get("resume_status"):
                pass  # 状态保留
            # 如果 candidate_info 有 ai_score 也可以使用
            if info.get("ai_score"):
                pass

        # ===== 构建标准 resume dict =====
        result = {
            "resume_id": rid,
            "name": name,
            "gender": gender,
            "education": education,
            "location": location,
            "expected_location": expected_city,
            "_mysql_source_file_path": row.get("source_file_path"),
            "_mysql_fetched_by": row.get("fetched_by",""),
            "_mysql_file_name": row.get("resume_name"),
            "total_work_years": total_work_years,
            "industry_tags": industry_tags or ["医疗健康"],
            "certifications": certifications,
            "skills": skills,
            "expected_salary": salary,
            "current_company": current_company,
            "current_position": current_position,
            "work_experiences": work_experiences,
            "projects": projects,
            "raw_text": raw_text,
            "source": "mysql_candidate_resume",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "sha256": sha256_of_text(rid),
            "status": STATUS_NEW,
            "routing": {},
            "rq": {},  # 引擎自行计算
            # ===== 业务同事要求的额外评分字段（保留原始值） =====
            "_mysql_phone": phone,
            "_mysql_email": email,
            "_mysql_uid": row.get("id"),
            "_mysql_resume_parse_time": row.get("resume_parse_time"),
            "_mysql_work_year_inf": row.get("work_year_inf"),
            "_mysql_work_start_time": row.get("work_start_time"),
            "_mysql_work_start_time_inf": row.get("work_start_time_inf"),
            "_mysql_work_pos_type_p": row.get("work_pos_type_p"),
            "_mysql_college_rank": row.get("college_rank"),
            "_mysql_college_type": row.get("college_type"),
            "_mysql_resume_integrity": row.get("resume_integrity"),
            "_mysql_job_stability": row.get("job_stability"),
            "_mysql_risks": row.get("risks"),
            "_mysql_highlights": row.get("highlights"),
            "_mysql_eval": row.get("eval"),
            "_mysql_profiler_result": row.get("profiler_result"),
            "_mysql_stability": row.get("stability"),
            "_mysql_capacity": row.get("capacity"),
            "_mysql_predicted_pos_types": row.get("predicted_pos_types"),
            "_mysql_predicted_industries_c1": row.get("predicted_industries_c1"),
            "_mysql_predicted_industries_c2": row.get("predicted_industries_c2"),
            "_mysql_expect_job_objs": row.get("expect_job_objs"),
            "_mysql_proj_exp_objs": row.get("proj_exp_objs"),
            "_mysql_training_objs": row.get("training_objs"),
            "_mysql_social_exp_objs": row.get("social_exp_objs"),
            "_mysql_lang_objs": row.get("lang_objs"),
            "_mysql_all_cert_objs": row.get("all_cert_objs"),
            "_mysql_cont_job_skill": row.get("cont_job_skill"),
            "_mysql_cont_proj_exp": row.get("cont_proj_exp"),
            "_mysql_source_file_path": row.get("source_file_path"),
            "_mysql_file_name": row.get("resume_name"),
            "_mysql_raw_json": row.get("raw_json"),
            "_mysql_info": {k: str(v) if hasattr(v, "isoformat") else v for k, v in (info or {}).items()} if info else None,  # candidate_info 完整记录
        }
        # Recruiter-Na imports retain their normalized facts in raw_json.  The
        # legacy columns above are populated by Alibaba/MinerU pipelines and
        # may be empty for a local-original import; never let a filename win
        # over the imported resume facts in that case.
        try:
            import_doc = json.loads(row.get("raw_json") or "{}")
            imported = import_doc.get("normalized_resume") if isinstance(import_doc, dict) else None
        except (json.JSONDecodeError, TypeError):
            imported = None
        if isinstance(imported, dict):
            for key in (
                "education", "location", "expected_location", "total_work_years", "industry_tags",
                "certifications", "skills", "expected_salary", "current_company", "current_position",
                "work_experiences", "projects", "routing", "status",
            ):
                if key in imported:
                    result[key] = imported[key]
            result["resume_id"] = str(imported.get("resume_id") or result["resume_id"])
            result["name"] = str(imported.get("name") or result["name"] or "未命名")
            result["raw_text"] = str(row.get("raw_text") or imported.get("raw_text") or "")
        return result

    def _get_info_by_phone(self, phone=""):
        """从 candidate_info 表读取人工修正后的候选人信息。"""
        if not phone:
            return None
        conn = self._connect()
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM {self.table_info} WHERE phone = %s LIMIT 1", (phone,))
        row = cur.fetchone()
        cur.close()
        return row

    def search(self, query="", **filters):
        conn = self._connect()
        cur = conn.cursor()
        where, params = [], []
        degree = filters.get("degree")
        if degree:
            where.append("degree LIKE %s"); params.append(f"%{degree}%")
        ym = filters.get("years_min")
        if ym is not None:
            try:
                where.append("work_year >= %s"); params.append(int(ym))
            except (ValueError, TypeError):
                pass
        loc = filters.get("location")
        limit = int(filters.get("limit") or 50)
        # When lexical retrieval is requested, fetch a sufficiently wide pool
        # before post-filtering. Retrieval may select candidates; it never
        # assigns semantic evidence levels or scores.
        tags_filter = filters.get("tags") or []
        certs_filter = filters.get("certs") or []
        qwords = [w for w in query.split() if w] if query else []
        sql_limit = max(limit * 20, 500) if (tags_filter or certs_filter or qwords or loc) else limit
        sql = "SELECT * FROM " + self.table_resume
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT " + str(sql_limit)
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        results = [self._resume_row_to_dict(r) for r in rows]
        # 额外过滤：current location / tags / certs / qwords
        # NOTE: hometown/籍贯 must never be used as current location.
        if loc or tags_filter or certs_filter or qwords:
            filtered = []
            for r in results:
                ok = True
                if loc and str(loc).lower() not in str(r.get("location") or "").lower():
                    ok = False
                if ok and tags_filter:
                    hay = (" ".join(r.get("skills",[])) + " " + " ".join(r.get("industry_tags",[]))
                           + " " + (r.get("raw_text") or "")).lower()
                    if not all(str(t).lower() in hay for t in tags_filter):
                        ok = False
                if ok and certs_filter:
                    have = {c.lower() for c in (r.get("certifications") or [])}
                    if not all(c.lower() in have for c in certs_filter):
                        ok = False
                if ok and qwords:
                    import json as _json
                    hay = (_json.dumps(r, ensure_ascii=False) + " " + " ".join([
                        str(r.get("name","")), str(r.get("current_position","")),
                        " ".join(r.get("skills") or []),
                        " ".join(r.get("industry_tags") or []),
                        " ".join(r.get("certifications") or []),
                        str(r.get("raw_text") or "")
                    ])).lower()
                    if not all(w in hay for w in qwords):
                        ok = False
                if ok:
                    filtered.append(r)
            results = filtered[:limit]
        return results

    def get(self, resume_id):
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(f"SELECT * FROM {self.table_resume} WHERE id = %s", (int(resume_id),))
        except ValueError:
            cur.execute(f"SELECT * FROM {self.table_resume} WHERE name = %s", (resume_id,))
        row = cur.fetchone()
        cur.close()
        return self._resume_row_to_dict(row) if row else None

    def upsert(self, resume):
        """MySQL 后端只读。写入本地缓存 workspace/mysql_cache.json"""
        cache_path = Path("workspace/mysql_cache.json")
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {"resumes": []}
            cache["resumes"] = [r for r in cache["resumes"] if r.get("resume_id") != resume.get("resume_id")]
            cache["resumes"].append(resume)
            cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[警告] MySQL 缓存写入失败: {e}")
        return resume

    def ingest_original(self, path: str | Path, *, source_root: str | Path, fetched_by: str = "recruiter-na-import") -> dict[str, Any]:
        """Insert one locally retained original into the production resume authority.

        This is deliberately separate from ``upsert``: the historic backend is
        read-only for generic normalized records, while an imported original
        needs an auditable source path, content digest, and parsed text in the
        same MySQL row that the delivery flow later resolves.
        """
        from rn.mail_watch import _extract_docx_or_pdf
        from rn.resume_db import parse_text_resume

        source = Path(path).resolve()
        root = Path(source_root).resolve()
        if not source.is_file() or not source.is_relative_to(root):
            raise ValueError("IMPORT_SOURCE_OUTSIDE_ROOT")
        if source.suffix.lower() not in {".pdf", ".docx"}:
            raise ValueError("IMPORT_UNSUPPORTED_FORMAT")
        blob = source.read_bytes()
        digest = hashlib.sha256(blob).hexdigest()
        text = _extract_docx_or_pdf(source)
        if not text or text.startswith("[DOC_PARSE_PENDING]") or len(text.strip()) < 20:
            raise ValueError("IMPORT_TEXT_UNAVAILABLE")

        parsed = parse_text_resume(
            text, resume_id="R-" + digest[:10], source="uploaded_original",
            created_at=time.strftime("%Y-%m-%dT%H:%M:%S"), meta={"filename": source.name},
        )
        # The existing production schema has short display/lookup columns.
        # Extraction is allowed to be noisy; retain the authoritative resume
        # body for evidence and clamp only denormalized convenience fields.
        def clipped(value: Any, limit: int) -> str:
            return str(value or "").strip()[:limit]

        stored_text = text[:16_000]
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                f"SELECT id FROM {self.table_resume} "
                "WHERE JSON_UNQUOTE(JSON_EXTRACT(raw_json, '$.recruiter_na_import.sha256')) = %s LIMIT 1",
                (digest,),
            )
            existing = cur.fetchone()
            if existing:
                return {"status": "duplicate", "id": int(existing["id"]), "sha256": digest, "filename": source.name}

            education = parsed.get("education") or {}
            payload = {
                "recruiter_na_import": {
                    "sha256": digest, "source": "uploaded_original", "filename": source.name,
                    "imported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                },
                "normalized_resume": parsed,
            }
            cur.execute(
                f"INSERT INTO {self.table_resume} "
                "(name, degree, major, work_year, work_company, work_position, resume_name, raw_text, source_file_path, fetched_by, raw_json) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    clipped(parsed.get("name"), 64), clipped(education.get("level"), 32), clipped(education.get("major"), 128),
                    clipped(parsed.get("total_work_years"), 16), clipped(parsed.get("current_company"), 256),
                    clipped(parsed.get("current_position"), 128), clipped(source.name, 256), stored_text, str(source), clipped(fetched_by, 64),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            inserted = int(cur.lastrowid)
            conn.commit()
            return {"status": "inserted", "id": inserted, "sha256": digest, "filename": source.name}
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()

    def stats(self):
        conn = self._connect()
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) as total FROM {self.table_resume}")
        total = cur.fetchone()["total"]
        cur.close()
        return {"total": total, "by_status": {"NEW": total}, "backend": "mysql"}

    def all_resumes(self):
        conn = self._connect()
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM {self.table_resume} ORDER BY id DESC")
        rows = cur.fetchall()
        cur.close()
        return [self._resume_row_to_dict(r) for r in rows]

    def close(self):
        if self._conn and self._conn.open:
            self._conn.close()
