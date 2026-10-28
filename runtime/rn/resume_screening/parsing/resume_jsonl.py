r"""JSONL 简历库后端 — 读 recruiter-na-parsed-resumes 的 resumes.jsonl。

字段映射与 ResumeMySQLBackend._resume_row_to_dict 对齐,
保证 v5 evidence-evaluator 拿到的字段名一致。

数据来源: D:\迪安\人力AI工具\deepseek harness\projects\recruiter-na\workspace\real_resume_db\recruiter-na-parsed-resumes-20260923\resumes.jsonl
"""
from __future__ import annotations

import json
import os
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Optional

from rn.resume_screening.parsing.resume_db import ResumeBackend, STATUS_NEW, sha256_of_text


DEFAULT_JSONL = Path(
    r"D:\迪安\人力AI工具\deepseek harness\projects\recruiter-na\workspace\real_resume_db\recruiter-na-parsed-resumes-20260923\resumes.jsonl"
)

# 沿当前文件向上找项目根(向上到包含 .env.local 的目录)
_ROOT_HINT = Path(__file__).resolve()
ROOT_PROJECT: Path | None = None
for _parent in (_ROOT_HINT, *_ROOT_HINT.parents):
    if (_parent / ".env.local").exists() or (_parent / "im").is_dir():
        ROOT_PROJECT = _parent
        break


class ResumeJsonlBackend(ResumeBackend):
    """从 resumes.jsonl 读真实简历,产出与 MySQL 后端同结构的 resume dict。"""

    def __init__(self, path: str | Path | None = None):
        # 优先级:1) 显式 path 参数; 2) 环境变量 RESUME_JSONL_PATH(若存在且文件存在);
        # 3) 约定路径 (workspace/real_resume_db/recruiter-na-parsed-resumes-20260923/resumes.jsonl)
        # Windows PowerShell 5.1 走 SystemDefault codepage 注入中文路径会 mojibake,
        # 因此即使 env 存在但文件读不到,也回退到约定路径。
        #
        # _explicit 记住路径是不是调用方点名给的:_load 只在「非显式」时才允许
        # 自动改指默认库,否则 ResumeJsonlBackend("Z:/nope.jsonl") 会静默返回
        # 另一份 112 条的默认库,调用方拿到的是错的库却以为是自己要的那份。
        self._explicit = path is not None
        self._skipped_lines = 0
        self._cache: list[dict[str, Any]] | None = None
        if path is not None:
            self.path = Path(path)
            return
        env_path = os.environ.get("RESUME_JSONL_PATH")
        fallback_candidates = [
            DEFAULT_JSONL,
            (ROOT_PROJECT / "workspace/real_resume_db/recruiter-na-parsed-resumes-20260923/resumes.jsonl")
            if ROOT_PROJECT
            else None,
        ]
        if env_path:
            ep = Path(env_path)
            if ep.exists():
                self.path = ep
                return
            # env 给的路径读不到(mojibake 等),降级
        self.path = next(
            (p for p in fallback_candidates if p and p.exists()),
            DEFAULT_JSONL,
        )

    def _load(self) -> list[dict[str, Any]]:
        if self._cache is not None:
            return self._cache
        if not self.path.exists():
            if self._explicit:
                # 显式路径读不到就是不可用,不能偷偷换成默认库。
                raise RuntimeError(f"resume jsonl backend unavailable: {self.path} does not exist")
            redirect: Path | None = None
            for p in (
                DEFAULT_JSONL,
                (ROOT_PROJECT / "workspace/real_resume_db/recruiter-na-parsed-resumes-20260923/resumes.jsonl")
                if ROOT_PROJECT
                else None,
            ):
                if p and p.exists():
                    redirect = p
                    break
            if redirect is None:
                self._cache = []
                return self._cache
            self.path = redirect
        rows: list[dict[str, Any]] = []
        skipped = 0
        try:
            # utf-8-sig:文件来自 Word/Excel 再导出,带 BOM 时 utf-8 会把
            # 第一个候选整行变成无法 JSON 解码的行,stats() 只报 skipped,
            # 数据静默少一条。
            with self.path.open(encoding="utf-8-sig") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        skipped += 1
                        continue
                    if not isinstance(obj, dict):
                        # 裸数组/裸字符串等非对象行:同样只跳过这一行,
                        # 否则 obj.get() 会 AttributeError 并让整库不可用。
                        skipped += 1
                        continue
                    try:
                        rows.append(self._row_to_dict(obj))
                    except Exception:
                        # 单行抽取失败不能拖垮整库。
                        skipped += 1
        except (OSError, UnicodeDecodeError) as exc:
            # 非法 UTF-8 也会在这里抛出,并被上层包成 RESUME_BACKEND_UNAVAILABLE。
            raise RuntimeError(f"resume jsonl backend unavailable: {self.path}: {exc}") from exc
        self._skipped_lines = skipped
        self._cache = rows
        return rows

    def _row_to_dict(self, row: dict[str, Any]) -> dict[str, Any]:
        """把 jsonl 行映射成招聘娜标准 resume dict。"""
        name = str(row.get("name") or "").strip()
        major = str(row.get("major") or "").strip()
        degree = str(row.get("degree") or "").strip()
        raw_text = str(row.get("raw_text") or "").strip()
        resume_name = str(row.get("resume_name") or "").strip()
        rid = str(row.get("id") or "").strip()

        # ===== 教育 =====
        education: dict[str, Any] = {}
        if degree:
            education["level"] = degree
        if major and major not in ("最高学", "无"):
            education["major"] = major
        # 学校/学院只能从 raw_text 里粗略提取(常见 "XXX大学/学院")
        school = self._extract_school(raw_text)
        if school:
            education["school"] = school

        # ===== 从 raw_text 抽取地点 / 工作年限 / 期望薪资 =====
        location = self._extract_location(raw_text)
        expected_location = self._extract_expected_location(raw_text)
        total_work_years = self._extract_work_years(raw_text)
        expected_salary = self._extract_expected_salary(raw_text)
        current_position = self._extract_current_position(raw_text)
        current_company = self._extract_current_company(raw_text)

        # ===== 证书 / 技能(从 raw_text 关键字) =====
        certifications = self._extract_certs(raw_text)
        skills = self._extract_skills(raw_text)

        # ===== 行业 =====
        industry_tags = self._extract_industry_tags(raw_text)

        # ===== 工作经历(粗略提取含年份区间的行) =====
        work_experiences = self._extract_work_experiences(raw_text)

        # ===== resume_id 用 sql 主键 =====
        if not rid:
            rid = "JSONL-" + sha256_of_text(name + resume_name)[:12]

        return {
            "resume_id": rid,
            "name": name or "未命名",
            "gender": "",
            "education": education,
            "location": location,
            "expected_location": expected_location,
            "total_work_years": total_work_years,
            "industry_tags": industry_tags,
            "certifications": certifications,
            "skills": skills,
            "expected_salary": expected_salary,
            "current_company": current_company,
            "current_position": current_position,
            "work_experiences": work_experiences,
            "projects": [],
            "raw_text": raw_text,
            "source": "jsonl_parsed_resumes",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "sha256": sha256_of_text(raw_text or name),
            "status": STATUS_NEW,
            "routing": {},
            "rq": {},
            # 原始 jsonl 行保留(便于追溯)
            "_jsonl_resume_name": resume_name,
            "_jsonl_uid": row.get("id"),
        }

    # ===== raw_text 字段抽取辅助 =====
    # 校名后缀。裸「理工」不在这里:'血库交叉配血及血液制品管理工' 里也会
    # 命中「理工」,而北京理工/大连理工这类名字必然以「大学」结尾,已被覆盖;
    # 只在下面的弱规则里用带否定环视的「理工」兜底。
    _SCHOOL_SUFFIX = (
        "大学", "学院", "职业技术学校", "卫生学校", "中专", "技校", "校区",
    )

    @staticmethod
    def _extract_school(raw_text: str) -> str:
        """抽毕业院校名。

        三条实测教训(112 行真实语料):
        1. 必须锚在行首/分隔符之后 —— 不锚会把同行日期一起吞进来,
           '2020.09-2024.06华中科技大学同济医学院' 整条都被当成校名。
        2. 不能用裸「理工」做后缀 —— 实测抽出 '4、参与血库交叉配血及血液制品管理工'
           和 '负责试剂与耗材的管理工'(本语料 3/112 行)。
        3. Word/HTML 导出会带 <table><tr><td> / colspan="4"> 前缀,必须剥标签。
        """
        import re
        delim = r"""[\n|｜,，;；:：>"《》]"""
        # 校名可能与日期同行且中间没有分隔符
        # ('2020.09-2024.06华中科技大学同济医学院'),所以锚也接受日期前缀。
        anchor = rf"(?:^|{delim}|20\d{{2}}[./-]\d{{1,2}}[./-]?\d{{0,2}})"
        body = r"""[^\s:：|｜,，;；<>"'()\[\]]"""
        candidates: list[str] = []
        # 量词用贪婪 {2,24} 而不是懒惰 {2,24}?:懒惰会在第一个「大学」就收工,
        # '华中科技大学同济医学院' 只剩 '华中科技大学'。
        m = re.search(
            rf"{anchor}\s*({body}{{2,24}}(?:{'|'.join(ResumeJsonlBackend._SCHOOL_SUFFIX)}))",
            raw_text,
            re.M,
        )
        if m:
            candidates.append(m.group(1))
        # 弱规则:名字本身以「理工」结尾(如 '大连理工'),但前面不能是「管」,
        # 后面 3 字内不能出现「作/程」——否则会命中「管理工作」。
        weak = re.search(r"([\u4e00-\u9fff]{2,8}?)(?<!管)理工(?![\u4e00-\u9fff]{0,3}[作程])", raw_text)
        if weak:
            candidates.append(weak.group(1) + "理工")
        for cand in candidates:
            name = re.sub(r"<[^>]*>", "", cand)                                  # 剥 HTML 标签
            name = re.sub(r"^\s*(?:19|20)\d{2}[^\u4e00-\u9fff]{0,12}", "", name)  # 剥日期前缀
            name = re.sub(r"^(?:毕业于|就读于|学校[：:])", "", name).strip()
            if 4 <= len(name) <= 20 and re.search(r"[\u4e00-\u9fff]", name):
                return name
        return ""

    @staticmethod
    def _extract_location(raw_text: str) -> str:
        """从多种字段名抽取当前城市。

        不使用 '籍贯' / '户籍' / '户口'(那是出生地)。
        关键词:现所在地 / 现居 / 现住 / 现工作地 / 现居住 / 居住地 / 工作地点 / 工作城市 / 地点 / 工作地
        兜底:第一行若匹配 '| 城市' 模式(简历模板的"| XX | YY岁 | NN年经验 | 城市")
        """
        import re
        for kw in (
            "现所在地", "现居地", "现住地", "现工作地", "现居城市", "现居住地",
            "居住地", "现居住城市", "工作地点", "工作城市", "工作地",
        ):
            m = re.search(rf"{kw}\s*[:：]\s*([^\s|｜,，\n]{{2,12}})", raw_text)
            if m:
                return m.group(1)
        # 兜底:行首 '| 性别 | YY岁 | NN年工作经验 | 城市'(应届邦标准模板)
        # 必须 NN>=3 才信(避免 "0 年" 或 "0 月" 噪声)
        m = re.search(
            r"\|\s*(?:男|女)\s*\|\s*(\d{1,2})\s*岁\s*\|\s*(\d{1,2})\s*年\s*(?:工作)?(?:经验|年限)",
            raw_text,
        )
        if m:
            try:
                yrs = int(m.group(2))
                if yrs >= 3:  # 排除"实习 0 年" 噪声
                    # 城市在年龄和年份之后
                    tail_m = re.search(
                        rf"\|\s*(?:男|女)\s*\|\s*\d{{1,2}}\s*岁\s*\|\s*\d{{1,2}}\s*年[^|]*\|\s*([^|\s\.]+)",
                        raw_text,
                    )
                    if tail_m and len(tail_m.group(1)) <= 12:
                        return tail_m.group(1)
            except ValueError:
                pass
        # 兜底:"最近工作 ... | 城市"
        m = re.search(r"最近工作[\s\S]{0,300}?\|\s*([^|\s\.]+?)\s*(?:\||\n|$)", raw_text)
        if m and len(m.group(1)) <= 12 and not re.search(r"年|岁|月|号|日", m.group(1)):
            return m.group(1)
        return ""

    @staticmethod
    def _extract_expected_location(raw_text: str) -> str:
        """期望城市 / 期望地点 / 期望工作地 / 地点：/ 期望城市：。"""
        import re
        for kw in ("期望城市", "期望地点", "期望工作地", "意向城市", "意向地点",
                   "意向工作地", "期望岗位工作地", "期望工作地点"):
            m = re.search(rf"{kw}\s*[:：]\s*([^\s|｜,，\n]{{2,12}})", raw_text)
            if m:
                return m.group(1)
        # 求职意向段:地点： XX
        m = re.search(r"地点\s*[:：]\s*([^\s|｜,，\n]{2,20})", raw_text)
        if m:
            return m.group(1)
        return ""

    @staticmethod
    def _extract_work_years(raw_text: str) -> int:
        import re
        for pat in (
            # "26年工作经验" / "5 年工作经验" / "10 年工作经验"
            r"(\d{1,2})\s*年\s*工作(?:经验|年限|时长)",
            # "工作年限: 5年" / "工作时长: 4年"
            r"工作(?:经验|年限|时长)\s*[:：]?\s*(\d{1,2})\s*年",
            # "工作时长：4年"
            r"工作\s*时长\s*[:：]?\s*(\d{1,2})\s*年",
            # "工作年限 5 年"
            r"工作\s*年限\s*(\d{1,2})\s*年",
            # "5 年工作经验|求职意向" — 行首短格式
            r"^(\d{1,2})\s*年\s*工作(?:经验|年限)",
            r"^##\s+(\d{1,2})\s*年\s*工作",
        ):
            m = re.search(pat, raw_text, re.MULTILINE)
            if m:
                try:
                    return int(m.group(1))
                except ValueError:
                    pass
        # 兜底: "| 性别 | YY岁 | NN年工作经验"(模板头部)
        m = re.search(r"\|\s*(?:男|女)\s*\|\s*(\d{1,2})\s*岁\s*\|\s*(\d{1,2})\s*年", raw_text)
        if m:
            try:
                return int(m.group(2))
            except ValueError:
                pass
        return 0

    @staticmethod
    def _extract_expected_salary(raw_text: str) -> str:
        import re
        # "期望薪资：6k-10k" / "期望薪资：5000-7000/月"
        m = re.search(r"期望薪资\s*[:：]\s*([^\s,，\n]{2,20})", raw_text)
        return m.group(1) if m else ""

    # 「职位:」/「公司:」后面若为空(HTML 表格导出常见),正文下一行会被
    # \s* 吞进来当作值。这些是被实测污染到的取值,抽到就丢弃而不是回退。
    # 用 startswith 而不是「出现在前 4 字」:后者会把合法职位
    # '教育顾问' 一起误杀,所以这里只保留完整的标题词。
    _SECTION_HEADER_NOISE = (
        "最高学历", "学历", "学位", "学校", "毕业", "公司", "部门", "名称",
        "时间", "时长", "薪资", "电话", "邮箱", "自我评价", "求职意向",
        "教育背景", "教育经历", "教育情况",
    )
    _ORG_SUFFIX_NOISE = ("公司", "集团", "厂", "中心", "医院", "学校", "学院")

    @staticmethod
    def _is_section_header(value: str) -> bool:
        """判断抽到的值其实是段落标题,而不是职位/公司名。"""
        import re
        if value.startswith(ResumeJsonlBackend._SECTION_HEADER_NOISE):
            return True
        return bool(re.search(r"^(?:共|合计|以上|无|暂无)", value))

    @staticmethod
    def _extract_current_position(raw_text: str) -> str:
        import re
        # 取简历里最近的 "工作描述" 上面紧邻的一行作为当前职位。
        # 冒号后的空白必须用 [ \t]*:用 \s* 会跨行,当「职位:」所在行没有值时
        # 会把下一行的段落标题抽成职位(实测抽到 '最高学历/学位')。
        # 用 finditer 而不是「换下一个关键词」:实测
        # '职位：最高学历\n职位：检验技师' 会被第一个坏值带偏成空。
        for kw in ("最近工作", "工作经历", "工作经验"):
            for sec in re.finditer(rf"{kw}[\s\S]{{0,200}}", raw_text):
                for m in re.finditer(
                    r"职位[：:][ \t]*([^\n：:、|；，,]{2,30})", sec.group(0)
                ):
                    value = m.group(1).strip()
                    # 抽到单位名(单行写法 "职位: 医学检验公司: 医疗公司派驻")时丢弃
                    if not value or ResumeJsonlBackend._is_section_header(value):
                        continue
                    if value.endswith(ResumeJsonlBackend._ORG_SUFFIX_NOISE):
                        continue
                    return value
        return ""

    @staticmethod
    def _extract_current_company(raw_text: str) -> str:
        import re
        # 必须和 _extract_current_position 用同一套关键词循环与守卫,否则同一份
        # 简历里 position 能抽到、company 抽不到(实测 '工作经历\n公司：杭州明康
        # 检验中心' → position 有值而 company 为空)。
        for kw in ("最近工作", "工作经历", "工作经验"):
            for sec in re.finditer(rf"{kw}[\s\S]{{0,200}}", raw_text):
                for m in re.finditer(
                    r"公司[：:][ \t]*([^\n：:、|；，,]{2,40})", sec.group(0)
                ):
                    value = m.group(1).strip()
                    # 单行 HTML 写法 "公司: 医疗公司派驻武汉一医院行业:医疗/护理"
                    # 会把后面的字段名一起吞进来,按字段名截断
                    for label in ("行业", "部门", "专业", "学校", "学历", "职能", "工作类型", "薪资"):
                        cut = value.find(label)
                        if cut > 1:
                            value = value[:cut]
                    if not value or ResumeJsonlBackend._is_section_header(value):
                        continue
                    return value
        return ""

    # 证书 token 里的噪声词:获奖、能力描述、考取动作等。
    # 原来用 "V"/"W"/"2"/"3" 这种单字符挡噪声,代价是把「普通话二级甲等」
    # 「计算机二级」这类合法证书一起挡掉了——单字符只会出现在证书名中间。
    _CERT_STOP_TOKENS = (
        "荣获", "获得", "获奖", "一等奖", "二等奖", "三等奖", "优秀奖", "先进个人",
        "技能大赛", "大赛", "参赛", "考取", "颁发", "编号", "并得",
        "具备", "熟悉", "熟练", "掌握", "能够", "良好", "较强", "希望", "通过",
        "本人", "找一份", "岗位职责",
        # 以下 9 个是 112 行语料里实测到的整句职责,不带它们时它们会
        # 因为含「检验/医师/技师」而被当成证书名写进人工决策证据。
        "精通", "符合", "持有", "年度", "一线", "实操", "流程", "确保", "负责",
        "从事", "承担", "要求", "规范", "经验",
        # 跨行窗口恢复后实测到的整句职责:'为临床诊断提供准确可靠的检验报告'
        "提供", "出具", "审核", "报告", "诊断", "完成", "提高", "服务",
    )
    _CERT_HEADINGS = ("资格证书", "职业证书", "证书")

    # 行业标签按 raw_text 关键词派生。恒定返回 ["医疗健康"] 会让
    # search(tags=["医疗健康"]) 对全库恒真,行业过滤等于没生效。
    _INDUSTRY_KEYWORDS = (
        ("医疗健康", ("医院", "检验", "护士", "医师", "医学", "药师", "诊疗", "口腔", "康复", "疾控")),
        ("金融", ("银行", "证券", "保险", "基金", "信贷", "风控", "理财")),
        ("制造业", ("机械", "电气", "车间", "装配", "焊接", "数控", "生产管理")),
        ("教育", ("教师", "教学", "师范", "培训机构", "教务")),
        # 「软件」「运维」不设为本行业的关键词:医疗简历里「LIS 软件」「设备运维」
        # 会把 35 份医疗简历误标成互联网。
        ("互联网", ("前端", "后端", "算法工程师", "测试工程师", "互联网公司", "APP开发")),
        ("建筑工程", ("建筑", "施工", "监理", "土建", "预算员", "造价")),
    )

    @staticmethod
    def _extract_industry_tags(raw_text: str) -> list[str]:
        """取第一个命中的行业(优先级从上到下),没命中回落到医疗健康。

        只取一个:候选库是医疗口径,叠加多个标签会让 tags 过滤失去区分度。
        """
        for tag, keys in ResumeJsonlBackend._INDUSTRY_KEYWORDS:
            if any(k in raw_text for k in keys):
                return [tag]
        return ["医疗健康"]

    @staticmethod
    def _extract_certs(raw_text: str) -> list[str]:
        """从资格证书段落提取证书名。

        三条实测教训(112 行真实语料):
        1. 取证窗口必须从**关键字之后**开始。原来 re.search(rf"{kw}[\\s\\S]{{0,500}}")
           让窗口以关键字本身开头,分词后「资格证书」四个字成了第一个 token,
           而它又含「资格」所以必然被收下 —— 28/112 行 certifications[0]
           就是 '资格证书'。
        2. 窗口要跨行(证书常常一行一条),但逐 token 过停用词:实测 12/112 行的
           「证书」是 '两年门诊检验科一线实操与方舱核酸批量检测' 这种 20 字职责句。
        3. token 长度上限 16、且命中停用词就丢。
        """
        import re
        certs: list[str] = []
        for kw in ResumeJsonlBackend._CERT_HEADINGS:
            m = re.search(kw, raw_text)
            if not m:
                continue
            # 从关键字之后取,这样 '资格证书' 本身不会进 token
            seg = raw_text[m.end():m.end() + 200]
            for w in re.findall(r"[\u4e00-\u9fff（）()A-Za-z0-9·\./\-]{3,16}", seg):
                if w in ResumeJsonlBackend._CERT_HEADINGS:
                    continue
                if any(k in w for k in ("检验", "护士", "执业", "医师", "技师", "技士",
                                        "资格", "英语", "普通话", "驾驶证", "计算机",
                                        "四级", "六级", "PGS", "PSC", "ISO")):
                    if any(bad in w for bad in ResumeJsonlBackend._CERT_STOP_TOKENS):
                        continue
                    if w not in certs:
                        certs.append(w)
            if certs:
                break
        return certs[:6]

    @staticmethod
    def _extract_skills(raw_text: str) -> list[str]:
        """从 raw_text 抓取技能关键字(医学检验类)。"""
        import re
        skills: list[str] = []
        keys = ("血常规", "尿常规", "粪常规", "生化", "免疫", "微生物", "PCR",
                "电泳", "细胞", "质控", "ISO15189", "生物安全", "上机检测",
                "标本接收", "报告审核", "ISO", "15189", "核酸")
        for k in keys:
            if k in raw_text and k not in skills:
                skills.append(k)
        return skills[:12]

    # 校名/教育段落名不得作为「公司」。即使日期与校名同行(校名 2024.9--2026.7)
    # 也要挡住,否则教育经历会整段进入 work_experiences。
    _EDU_ORG_NOISE = (
        "大学", "学院", "学校", "中专", "职校", "技校", "高中", "初中",
        "研究生院", "教育背景", "教育经历", "教育情况", "学历", "学位",
        "专业", "毕业", "函授", "自考",
        # 「培训」已移出:实测 '浙江华信职业培训中心 2018.01-2020.01 招生主管'
        # 是真雇主,被这里整条丢掉。其余教育词足够挡住教育经历段落。
    )

    # 取「紧邻下一行」当职位时额外挡掉的学历行(校名/专业等已在 _EDU_ORG_NOISE)。
    # 学历词几乎不会出现在职位名里,用于挡住 '医学检验技术(本科)' 这类行。
    _EDU_LINE_NOISE = (
        "本科", "硕士", "博士", "大专", "专科", "学士", "研究生",
        "双学位", "统招", "全日制", "在校", "应届",
    )

    # 紧邻取值时额外挡掉的正文形态:编号条目、能力/职责描述、日期区间、
    # 段落标题、教育机构名、学历行。
    _NEXT_LINE_STOP = (
        "20\\d{2}\\s*[-—–~至到]", "具备", "熟悉", "熟练", "掌握", "能够",
        "负责", "主要", "参与", "协助", "承担", "从事", "确保",
    )

    @staticmethod
    def _usable_next_line(line: str, *, max_len: int = 30) -> bool:
        """判断「紧邻下一行」能不能当 company/position 用。"""
        import re
        return bool(
            line
            and len(line) <= max_len
            and not re.match(r"^[\d①-⑳•·\-*、]", line)
            and not any(re.search(p, line) for p in ResumeJsonlBackend._NEXT_LINE_STOP)
            and not ResumeJsonlBackend._is_section_header(line)
            and not any(w in line for w in ResumeJsonlBackend._EDU_ORG_NOISE)
            and not any(w in line for w in ResumeJsonlBackend._EDU_LINE_NOISE)
        )

    @staticmethod
    def _clean_company(value: str) -> str:
        """公司名清洗:剥 HTML 标签、剥首尾噪声符号、拒绝纯符号/日期碎片。

        实测语料里 '临检组（2020.01-2022.01）' 的剩余段是 '）'、
        '## 2024.01-...' 的前缀是 '##'——它们会原样进人工决策证据。
        """
        import re
        if not isinstance(value, str):
            return ""
        value = re.sub(r"<[^>]*>", "", value)
        value = re.sub(r"\s+", " ", value).strip(" |，,、()（）:：-—[]【】#*")
        if len(value) < 2 or not re.search(r"[A-Za-z一-鿿]{2}", value):
            return ""
        return value

    @staticmethod
    def _looks_like_position_token(value: str) -> bool:
        """短词是否是职位而不是日期/编号残片。"""
        import re
        return bool(
            value
            and len(value) <= 12
            and not value[0].isdigit()
            and re.search(r"[A-Za-z一-鿿]{2}", value)
        )

    @staticmethod
    def _extract_work_experiences(raw_text: str) -> list[dict[str, Any]]:
        """抓取含 20XX.XX 区间 的工作经历行。

        空白一律用 [ \\t]* 而不是 \\s*:[ \\t]* 之外的 \\s* 会跨行,实测会把
        日期区间的「上一行」当 company(教育背景里的学校)、把「下一行」正文首词
        当 position('1.具备熟练操作日立')。两处都会随 screening_v5 的证据链
        进入人工决策,所以这里必须锁死行边界。

        真实语料 112 行里有 98 行的日期在**行首**(要么与公司同行
        '2020.03-2022.06 杭州明康检验中心 检验技师',要么日期独占一行、
        公司在下一行),原来的写法在这两种形态上 group(1) 为空就整条丢弃,
        98 行里 81 行零产出。所以空 company 时要按下面的顺序补救:
        同行剩余文本 → 下一行。
        """
        import re
        out: list[dict[str, Any]] = []
        # 日期分隔符必须容错:真实语料里存在 '2021..9--2024.7'(两个点)和
        # '2024.9--2026.7',写成 [/.-]{1} 会整行漏掉。
        for m in re.finditer(
            r"([^\n]{0,60}?)[ \t]*\(?(20\d{2})[/.\-]{1,3}[ \t]*(\d{1,2})?[ \t]*"
            r"[-—–~至到]+[ \t]*(20\d{2}|至今)[/.\-]{0,2}[ \t]*(\d{1,2})?\)?[ \t]*([^\n]{0,80})",
            raw_text,
        ):
            company = ResumeJsonlBackend._clean_company(m.group(1))
            start = m.group(2) + (f"-{m.group(3):0>2}" if m.group(3) else "")
            end = m.group(4) + (f"-{m.group(5):0>2}" if m.group(5) and m.group(4) != "至今" else "")
            rest = m.group(6).strip()
            # 「1. 怀化市妇幼保健院 - 实验支持专员」这种编号必须先剥掉,
            # 否则编号会跟着公司名一起进人工决策证据。
            company = re.sub(r"^[\d①-⑳]+\s*[.、)）]\s*", "", company)
            rest_parts = rest.split(None, 1)
            # 「公司 职位 日期」同一行时,日期前缀里的第二个短词是职位
            # (实测 '安徽广瑞医疗管理有限公司 医学检验 2024.08-2026.07')
            inline_position = ""
            if company and len(company.split()) > 1:
                parts = company.split()
                if ResumeJsonlBackend._looks_like_position_token(parts[-1]):
                    inline_position = parts[-1]
                    company = ResumeJsonlBackend._clean_company(" ".join(parts[:-1]))
            position = inline_position
            if not company and rest_parts:
                # 日期在行首:同行剩余文本的第一段才是公司名,第二段是职位
                company = ResumeJsonlBackend._clean_company(rest_parts[0])
                if len(rest_parts) > 1 and rest_parts[1].split():
                    inline_pos = ResumeJsonlBackend._clean_company(rest_parts[1].split()[0])
                    if inline_pos and ResumeJsonlBackend._looks_like_position_token(inline_pos):
                        position = inline_pos
            elif company:
                # 公司在日期之前:日期之后的剩余文本首词是职位
                if rest_parts and ResumeJsonlBackend._looks_like_position_token(rest_parts[0]):
                    position = ResumeJsonlBackend._clean_company(rest_parts[0])
            else:
                # 日期独占一行:公司名在紧邻的上一行或下一行。**上一行是教育
                # 段落时必须整条丢掉**——教育背景的排版是
                # '教育背景 / 云南经济管理学院 / 2024.9--2026.7 / 医学检验技术',
                # 公司既不在日期前也不在日期后,只有靠上一行的教育词才挡得住,
                # 否则会把专业名当公司(这正是缺陷 #1 的原始现象)。
                head = raw_text[:m.start()]
                prev_line = head.rsplit("\n", 2)[-2].strip() if "\n" in head else ""
                if any(word in prev_line
                       for word in ResumeJsonlBackend._EDU_ORG_NOISE
                       + ResumeJsonlBackend._EDU_LINE_NOISE):
                    continue
                tail = raw_text[m.end():]
                lines = tail.split("\n", 3)
                nxt = lines[1].strip() if len(lines) > 1 else ""
                if not ResumeJsonlBackend._usable_next_line(nxt):
                    continue
                company = ResumeJsonlBackend._clean_company(nxt)
                if len(lines) > 2 and ResumeJsonlBackend._usable_next_line(lines[2].strip()):
                    position = ResumeJsonlBackend._clean_company(lines[2].strip())
            if not company or any(word in company for word in ResumeJsonlBackend._EDU_ORG_NOISE):
                continue
            if ResumeJsonlBackend._is_section_header(position):
                position = ""
            if not position and company != (rest_parts[0] if rest_parts else ""):
                # 「公司 日期」与「职位」分行时,只向前看紧邻的**一整行**(有界,
                # 不像 \s* 那样跨多行)
                tail = raw_text[m.end():]
                nxt = tail.split("\n", 2)[1].strip() if "\n" in tail else ""
                if ResumeJsonlBackend._usable_next_line(nxt):
                    position = ResumeJsonlBackend._clean_company(nxt)
            out.append({
                "company": company,
                "position": position,
                "start_date": start,
                "end_date": end or "至今",
                "description": rest_parts[1] if len(rest_parts) > 1 else "",
            })
        return out[:6]

    # ===== ResumeBackend 接口 =====
    def search(self, query: str = "", **filters: Any) -> list[dict[str, Any]]:
        rows = self._load()
        results = list(rows)
        degree = filters.get("degree")
        years_min = filters.get("years_min")
        location = filters.get("location")
        tags = filters.get("tags") or []
        certs = filters.get("certs") or []
        limit = int(filters.get("limit") or 50)

        def _degree_rank(level: str) -> int:
            table = {"高中": 1, "中专": 1, "大专": 2, "专科": 2,
                     "本科": 3, "学士": 3, "硕士": 4, "研究生": 4, "博士": 5}
            for k, r in table.items():
                if k in level:
                    return r
            return 0

        out: list[dict[str, Any]] = []
        for r in results:
            if degree and _degree_rank(str(r.get("education", {}).get("level", ""))) < _degree_rank(str(degree)):
                continue
            if years_min is not None and int(r.get("total_work_years") or 0) < int(years_min):
                continue
            if location and str(location).lower() not in str(r.get("location") or "").lower():
                # location 匹配宽松:city substring
                continue
            if tags:
                hay = (" ".join(r.get("skills") or []) + " " + " ".join(r.get("industry_tags") or []) + " " + str(r.get("raw_text") or "")).lower()
                if not all(str(t).lower() in hay for t in tags):
                    continue
            if certs:
                have = {str(c).lower() for c in (r.get("certifications") or [])}
                if not all(str(c).lower() in have for c in certs):
                    continue
            if query:
                qwords = [w for w in query.split() if w]
                hay = (json.dumps(r, ensure_ascii=False) + " " + " ".join([
                    str(r.get("name", "")), str(r.get("current_position", "")),
                    " ".join(r.get("skills") or []),
                    str(r.get("raw_text") or "")
                ])).lower()
                if not all(w.lower() in hay for w in qwords):
                    continue
            out.append(r)
        return out[:limit]

    def get(self, resume_id: str) -> Optional[dict[str, Any]]:
        for r in self._load():
            if str(r.get("resume_id")) == str(resume_id):
                return r
        return None

    def upsert(self, resume: dict[str, Any]) -> dict[str, Any]:
        """jsonl 后端只读:返回原值,不写文件。"""
        return resume

    def stats(self) -> dict[str, Any]:
        rows = self._load()
        return {"total": len(rows), "by_status": {"NEW": len(rows)},
                "skipped_lines": self._skipped_lines, "backend": "jsonl"}

    def all_resumes(self) -> list[dict[str, Any]]:
        return list(self._load())

    def count(self) -> int:
        return len(self._load())


# ===== raw_text 兜底抽取(带 LRU 缓存)=====
# MySQL 后端每个检索词都会把同一批 ~500 行全量转换一遍,而一次筛选有多个
# 检索词,于是同一份 raw_text 要跑 10 次全量正则(地点/年限/薪资/职位/公司/
# 经历/技能/证书)。按 raw_text 哈希缓存后,第 2 个检索词起几乎免费。
# 注意缓存的是「抽取结果」而不是 resume dict:resume dict 会被调用方就地改写
# (routing/rq),共享同一个对象会把一份候选的改动泄漏给另一份检索结果。
_RAW_TEXT_FALLBACK_CACHE: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
# 容量必须 ≥ 单次检索能拉到的 distinct raw_text 数,否则缓存命中率是 0:
# screening_v5 城市探测走 RECRUITER_SCREEN_CITY_PROBE_LIMIT=200,
# resume_mysql.search 的 sql_limit = max(limit*20, 500) → 4000。
# 512 在生产路径上一次都命中不了(实测 3 query × 4000 → 12000 次全 miss)。
_RAW_TEXT_FALLBACK_CACHE_MAX = 20000


def extract_profile_fallbacks(raw_text: str) -> dict[str, Any]:
    """从 raw_text 抽取简历缺列时需要的字段,结果带 LRU 缓存。

    永远返回全套键(值为空串 / 0 / [] 由抽取器自己决定),调用方可以直接用
    fb["location"] 取值 —— 早先对空 raw_text 返回 {} 会让
    resume_mysql._resume_row_to_dict 抛 KeyError。
    返回的可变容器都是新副本,调用方可就地修改。
    """
    import copy

    from rn.resume_screening.parsing.resume_db import sha256_of_text

    # 脏数据保护:两个后端的 raw_text 都来自 DB / JSON,NULL 会让正则直接
    # TypeError,所以在这里统一成 str。
    if not isinstance(raw_text, str):
        raw_text = "" if raw_text is None else str(raw_text)
    key = sha256_of_text(raw_text) if raw_text else ""
    cached = _RAW_TEXT_FALLBACK_CACHE.get(key)
    if cached is not None:
        _RAW_TEXT_FALLBACK_CACHE.move_to_end(key)
    else:
        cached = {
            "location": ResumeJsonlBackend._extract_location(raw_text),
            "expected_city": ResumeJsonlBackend._extract_expected_location(raw_text),
            "total_work_years": ResumeJsonlBackend._extract_work_years(raw_text),
            "salary": ResumeJsonlBackend._extract_expected_salary(raw_text),
            "current_position": ResumeJsonlBackend._extract_current_position(raw_text),
            "current_company": ResumeJsonlBackend._extract_current_company(raw_text),
            "work_experiences": ResumeJsonlBackend._extract_work_experiences(raw_text),
            "skills": ResumeJsonlBackend._extract_skills(raw_text),
            "certs": ResumeJsonlBackend._extract_certs(raw_text),
            "school": ResumeJsonlBackend._extract_school(raw_text),
        }
        _RAW_TEXT_FALLBACK_CACHE[key] = cached
        while len(_RAW_TEXT_FALLBACK_CACHE) > _RAW_TEXT_FALLBACK_CACHE_MAX:
            _RAW_TEXT_FALLBACK_CACHE.popitem(last=False)
    return {
        "location": cached["location"],
        "expected_city": cached["expected_city"],
        "total_work_years": cached["total_work_years"],
        "salary": cached["salary"],
        "current_position": cached["current_position"],
        "current_company": cached["current_company"],
        "work_experiences": copy.deepcopy(cached["work_experiences"]),
        "skills": list(cached["skills"]),
        "certs": list(cached["certs"]),
        "school": cached["school"],
    }