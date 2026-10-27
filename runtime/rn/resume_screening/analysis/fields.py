"""简历文本 → 指定字段的确定性抽取（resume_screening.analysis.fields）。

本模块是「简历文本 → 结构化简历事实」的纯确定性实现：

- 只使用标准库：不联网、不调用 LLM、不读写数据库、不读环境变量；
- 入口 :func:`extract_resume_fields` 返回固定键的 dict（见 ``RESUME_FIELD_KEYS``），
  供 ``tags.py`` / ``service.py`` / CLI 使用；模块之间只通过该 dict 契约耦合；
- :func:`looks_like_resume` 给 CLI 做「输入是否像简历」的粗判；
- 兼容中文 / 英文简历、PDF/Word 文本层以及 Windows OCR 输出（OCR 会在 CJK 字符
  之间插入空格、偶发把字拆成偏旁），因此匹配前先做归一化。

抽取结果全部是「待核实的简历事实」，不构成任何录用结论或数值评分。
"""
from __future__ import annotations

import datetime as _dt
import html as _html
import re
import unicodedata
from typing import Any, Callable, Iterable, Optional

__all__ = ["RESUME_FIELD_KEYS", "extract_resume_fields", "looks_like_resume"]

#: ``extract_resume_fields`` 返回 dict 的全部键（顺序即返回顺序）。
RESUME_FIELD_KEYS: tuple[str, ...] = (
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

# missing_fields 只统计「正文内容键」；raw_text / 元数据键永不缺失。
_CONTENT_KEYS: tuple[str, ...] = (
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
)


# ---------------------------------------------------------------------------
# 基础正则 / 常量
# ---------------------------------------------------------------------------

_CJK_RANGE = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_CJK_CHAR_RE = re.compile(f"[{_CJK_RANGE}]")
_CJK_ONLY_RE = re.compile(f"^[{_CJK_RANGE}]+$")
_CJK_GAP_RE = re.compile(f"(?<=[{_CJK_RANGE}])[ ]+(?=[{_CJK_RANGE}])")

_IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
# 上下标只用于脚注标记，内容极短；给 .*? 加上长度上限，避免未闭合 <sub> 时
# 正则向后扫描到文末导致的 O(n²)。
_HTML_BLOCK_RE = re.compile(r"<(sub|sup)\b[^>]*>.{0,200}?</\1>", re.IGNORECASE | re.DOTALL)
_HTML_TAG_RE = re.compile(r"</?[A-Za-z][^>]*>")
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_LEAD_MARK_RE = re.compile(r"^[\s#>*•·●○■□▪◆◇\-—–_=+~]+")
_LEAD_NUMBER_RE = re.compile(
    r"^(?:[（(]?[一二三四五六七八九十]+[)）]?[、.．]|[（(]?\d{1,2}[)）]?[、.．]|[（(]\d{1,2}[)）]|[①②③④⑤⑥⑦⑧⑨⑩])"
)

_MOBILE_RE = re.compile(r"(?<!\d)1[3-9]\d(?:[ \-]?\d){8}(?!\d)")
_LANDLINE_RE = re.compile(r"(?<!\d)(0\d{2,3}[ \-]?\d{7,8})(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")

# 日期区间：2021.07-2024.08 / 2021/07~/2024/08 / 2021年7月-2024年8月 / 2021-07 至今
_RANGE_RE = re.compile(
    r"(?P<sy>(?:19|20)\d{2})"
    r"(?:(?P<sd>[./\-年·])\s*(?P<sm>0?[1-9]|1[0-2])\s*月?)?"
    r"\s*(?:-|—|–|－|~|～|至|到|直到)\s*[/]?\s*"
    r"(?:(?P<ey>(?:19|20)\d{2})"
    r"(?:(?P<ed>[./\-年·])\s*(?P<em>0?[1-9]|1[0-2])\s*月?)?"
    r"|(?P<present>至今|现在|目前|今|present|now))",
    re.IGNORECASE,
)

# OCR 常见「拆字 / 形近字」修复（在删除 CJK 间空格之后执行）
_OCR_REPAIRS: tuple[tuple[str, str], ...] = (
    ("氵则", "测"),
    ("钅则", "测"),
    ("经厉", "经历"),
    ("简厉", "简历"),
    ("学厉", "学历"),
    ("资厉", "资历"),
    ("技木", "技术"),
    ("技朮", "技术"),
    ("英浯", "英语"),
    ("简万", "简历"),
    ("弓长", "张"),
)


def _norm_key(text: str) -> str:
    """键名归一：去空白、转小写、去尾部冒号。"""
    return re.sub(r"\s+", "", text or "").lower().strip(":：")


# ---------------------------------------------------------------------------
# 字段同义词表（KV 键名 → 内部字段）
# ---------------------------------------------------------------------------

_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("姓名", "名字", "中文名", "name", "full name", "candidate name"),
    "gender": ("性别", "gender", "sex"),
    "age": ("年龄", "age"),
    "phone": ("联系电话", "手机号码", "联系方式", "电话号码", "手机号", "手机", "电话", "phone", "mobile", "tel", "telephone"),
    "email": ("电子邮箱", "电子邮件", "邮箱", "邮件", "email", "e-mail", "mail"),
    "location": ("现居住地", "现所在地", "现居城市", "现居地", "所在地", "居住地", "所在城市", "住址", "地址", "现居", "location", "current city", "city", "address"),
    "education_level": ("最高学历/学位", "最高学历", "学历/学位", "学历学位", "全日制学历", "教育程度", "文化程度", "学历", "education level", "degree"),
    "degree_title": ("最高学位", "学位", "degree title", "academic degree"),
    "school": ("毕业院校名称", "毕业院校", "毕业学校", "就读学校", "学校", "院校", "school", "university", "college", "graduated from"),
    "major": ("专业名称", "所学专业", "主修专业", "专业", "major", "field of study", "specialization"),
    "grad_year": ("预计毕业时间", "毕业时间", "毕业年份", "毕业日期", "graduation year", "graduation date"),
    "work_years": ("累计工作年限", "工作经历年限", "工作经验", "工作年限", "从业年限", "经验年限", "工作时长", "工龄", "years of experience", "work years"),
    "current_company": ("当前公司", "目前公司", "现任公司", "所在公司", "工作单位", "现公司", "公司", "单位", "company", "current company", "employer"),
    "current_position": ("当前职位", "目前职位", "现任职位", "当前岗位", "所在职位", "现职位", "职位", "岗位", "position", "current position", "job title"),
    "expected_position": ("期望从事职位", "期望职位", "应聘职位", "应聘岗位", "意向岗位", "期望岗位", "目标职位", "求职岗位", "求职方向", "意向职位", "求职意向", "职能", "expected position", "career objective", "job objective", "desired position", "objective"),
    "expected_city": ("期望工作城市", "期望工作地点", "期望城市", "意向城市", "期望地点", "意向地点", "期望地区", "目标城市", "地点", "preferred city", "expected city", "desired city"),
    "expected_salary": ("期望薪资", "薪资要求", "期望月薪", "期望待遇", "薪资待遇", "期望年薪", "要求薪资", "expected salary", "salary expectation", "desired salary"),
    "certifications": ("职业资格证书", "执业证书", "资格证书", "专业证书", "资质证书", "所获证书", "证书荣誉", "证书", "certificates", "certifications", "certificate", "licenses", "qualifications"),
    "skills": ("专业技能特长", "专业技能", "技能特长", "技能技巧", "计算机技能", "个人技能", "职业技能", "技能", "skills", "professional skills", "technical skills"),
    "industry": ("所属行业", "行业领域", "意向行业", "行业", "industry", "industries"),
}

_ALIAS_INDEX: dict[str, str] = {}
for _field, _aliases in _FIELD_ALIASES.items():
    for _a in _aliases:
        _ALIAS_INDEX[_norm_key(_a)] = _field

# 允许「键值之间没有分隔符」的字段（OCR 常把「姓名 张三」粘成「姓名张三」）
_NO_SEP_FIELDS = ("name", "gender", "age", "phone", "email", "education_level", "degree_title")
_NO_SEP_ALIASES: tuple[tuple[str, str], ...] = tuple(
    sorted(
        ((_norm_key(a), f) for f, aliases in _FIELD_ALIASES.items() if f in _NO_SEP_FIELDS for a in aliases),
        key=lambda pair: -len(pair[0]),
    )
)


# ---------------------------------------------------------------------------
# 段落标题
# ---------------------------------------------------------------------------

_SECTION_LABELS: dict[str, str] = {
    "preamble": "开头（首个段落标题之前）",
    "basic": "基本信息",
    "job_intent": "求职意向",
    "education": "教育背景",
    "work": "工作经历",
    "internship": "实习经历",
    "projects": "项目经验",
    "skills": "专业技能",
    "certs": "证书",
    "self_eval": "自我评价",
    "other": "其他段落",
}

_ZH_SECTION_ALIASES: dict[str, str] = {
    # 基本信息 / 联系方式
    "个人信息": "basic",
    "基本信息": "basic",
    "个人资料": "basic",
    "个人基本信息": "basic",
    "基本资料": "basic",
    "个人档案": "basic",
    "联系方式": "basic",
    "联系信息": "basic",
    "最近工作": "other",
    # 求职意向
    "求职意向": "job_intent",
    "求职信息": "job_intent",
    "求职期望": "job_intent",
    "应聘意向": "job_intent",
    "求职目标": "job_intent",
    "求职岗位": "job_intent",
    "职业意向": "job_intent",
    "期望工作": "job_intent",
    # 教育
    "教育背景": "education",
    "教育经历": "education",
    "教育情况": "education",
    "学历背景": "education",
    "学历信息": "education",
    "学习经历": "education",
    "教育": "education",
    # 工作
    "工作经历": "work",
    "工作经验": "work",
    "职业经历": "work",
    "工作履历": "work",
    "工作背景": "work",
    "从业经历": "work",
    "工作经历与业绩": "work",
    # 实习
    "实习经历": "internship",
    "实习经验": "internship",
    "实践经历": "internship",
    # 注意：不要收录裸「实习」——它常是工作经历里的职位值（如「实习」），会误切断段落。
    # 项目
    "项目经验": "projects",
    "项目经历": "projects",
    "项目介绍": "projects",
    "项目实践": "projects",
    "科研经历": "projects",
    "科研项目": "projects",
    # 技能
    "专业技能": "skills",
    "技能特长": "skills",
    "技能技巧": "skills",
    "专业技能特长": "skills",
    "计算机技能": "skills",
    "专业能力": "skills",
    "能力特长": "skills",
    "个人技能": "skills",
    "职业技能": "skills",
    "技能": "skills",
    # 证书
    "证书": "certs",
    "资格证书": "certs",
    "职业资格证书": "certs",
    "荣誉证书": "certs",
    "所获证书": "certs",
    "资质证书": "certs",
    "证书荣誉": "certs",
    "获奖情况": "certs",
    "获奖经历": "certs",
    "荣誉奖项": "certs",
    "技能证书": "certs",
    "专业证书": "certs",
    "执业资格": "certs",
    # 自我评价
    "自我评价": "self_eval",
    "个人评价": "self_eval",
    "自我介绍": "self_eval",
    "个人优势": "self_eval",
    "个人总结": "self_eval",
    "自我描述": "self_eval",
    "个人简介": "self_eval",
    "自我鉴定": "self_eval",
    # 其他用于切断段落
    "主修课程": "other",
    "核心课程": "other",
    "语言能力": "other",
    "兴趣爱好": "other",
    "个人爱好": "other",
    "培训经历": "other",
    "校园经历": "other",
    "社团经历": "other",
    "校园实践": "other",
    "志愿者经历": "other",
    "志愿服务": "other",
    "社会实践": "other",
    "奖励情况": "other",
    "家庭成员": "other",
}

_EN_SECTION_ALIASES: dict[str, str] = {
    "personal information": "basic",
    "personal info": "basic",
    "basic information": "basic",
    "contact": "basic",
    "contact information": "basic",
    "contact details": "basic",
    "objective": "job_intent",
    "career objective": "job_intent",
    "job intention": "job_intent",
    "job objective": "job_intent",
    "employment objective": "job_intent",
    "education": "education",
    "education background": "education",
    "educational background": "education",
    "academic background": "education",
    "work experience": "work",
    "working experience": "work",
    "employment history": "work",
    "professional experience": "work",
    "work history": "work",
    "experience": "work",
    "internship": "internship",
    "internship experience": "internship",
    "internships": "internship",
    "practicum": "internship",
    "projects": "projects",
    "project experience": "projects",
    "project experiences": "projects",
    "research experience": "projects",
    "skills": "skills",
    "professional skills": "skills",
    "technical skills": "skills",
    "core competencies": "skills",
    "skill": "skills",
    "certificates": "certs",
    "certifications": "certs",
    "certificate": "certs",
    "licenses": "certs",
    "honors": "certs",
    "awards": "certs",
    "qualifications": "certs",
    "self-evaluation": "self_eval",
    "self evaluation": "self_eval",
    "self-assessment": "self_eval",
    "self assessment": "self_eval",
    "summary": "self_eval",
    "profile": "self_eval",
    "about me": "self_eval",
    "personal summary": "self_eval",
    "coursework": "other",
    "courses": "other",
    "interests": "other",
    "hobbies": "other",
    "languages": "other",
    "training": "other",
    "activities": "other",
}

_ZH_SECTION_INDEX: dict[str, str] = {_norm_key(k): v for k, v in _ZH_SECTION_ALIASES.items()}
_EN_SECTION_INDEX: dict[str, str] = {re.sub(r"\s+", " ", k.lower()): v for k, v in _EN_SECTION_ALIASES.items()}


# ---------------------------------------------------------------------------
# 学历 / 学位 / 学校
# ---------------------------------------------------------------------------

_LEVEL_RANK: dict[str, int] = {"博士": 5, "硕士": 4, "本科": 3, "大专": 2, "中专": 1, "高中": 1}

# (关键词, level, degree)；按长度降序参与匹配
_CN_LEVEL_TABLE: tuple[tuple[str, str, str], ...] = tuple(
    sorted(
        (
            ("博士研究生", "博士", "博士"),
            ("硕士研究生", "硕士", "硕士"),
            ("大学本科", "本科", ""),
            ("专升本", "本科", ""),
            ("研究生", "硕士", ""),
            ("博士", "博士", "博士"),
            ("硕士", "硕士", "硕士"),
            ("本科", "本科", ""),
            ("学士", "本科", "学士"),
            ("大专", "大专", ""),
            ("专科", "大专", ""),
            ("高职", "大专", ""),
            ("中专", "中专", ""),
            ("中技", "中专", ""),
            ("职高", "中专", ""),
            ("高中", "高中", ""),
        ),
        key=lambda row: -len(row[0]),
    )
)

_EN_LEVEL_TABLE: tuple[tuple[str, str, str], ...] = (
    ("phd", "博士", "博士"),
    ("doctorate", "博士", "博士"),
    ("doctor", "博士", "博士"),
    ("master", "硕士", "硕士"),
    ("mba", "硕士", "硕士"),
    ("bachelor", "本科", "学士"),
    ("undergraduate", "本科", "学士"),
    ("associate", "大专", ""),
)

_SCHOOL_KWS_CJK: tuple[str, ...] = tuple(
    sorted(
        ("职业技术学院", "技师学院", "职业学院", "技术学院", "大学", "学院", "学校", "中学", "高中", "研究院"),
        key=len,
        reverse=True,
    )
)
_EN_SCHOOL_RE = re.compile(r"\b(university|college|institute|academy|school)\b", re.IGNORECASE)

# ---------------------------------------------------------------------------
# 公司 / 职位关键词
# ---------------------------------------------------------------------------

_COMPANY_KWS_CJK: tuple[str, ...] = tuple(
    sorted(
        (
            "有限公司", "股份公司", "分公司", "公司", "集团", "事务所", "医院", "检验所",
            "检验中心", "实验室", "门诊部", "卫生院", "保健院", "疾控中心", "研究院",
            "研究所", "银行", "学校", "学院", "大学", "诊所", "药房", "血站", "防疫站",
            "科技", "生物", "医疗", "中心",
        ),
        key=len,
        reverse=True,
    )
)
_EN_COMPANY_RE = re.compile(
    r"\b(co\.?|ltd\.?|company|group|hospital|laboratory|lab|center|centre|institute|academy|university|college|bank|clinic|diagnostics|biotech)\b",
    re.IGNORECASE,
)
_EN_COMPANY_SUFFIX_RE = re.compile(
    r"(?:co|ltd|inc|corp|llc|company|group|hospital|laboratory|lab|center|centre|institute|university|college|bank|clinic|academy)\.?$",
    re.IGNORECASE,
)

_POSITION_KWS_CJK: tuple[str, ...] = (
    "工程师", "技术员", "检验师", "检验士", "技师", "主管", "经理", "专员", "助理",
    "实习生", "实习", "见习", "护士", "会计", "出纳", "销售", "代表", "顾问", "医生",
    "医师", "主任", "组长", "班长", "文员", "客服", "运营", "采购", "检验", "分析",
    "开发", "设计", "教师", "老师", "操作员", "质检员", "技术",
)
_EN_POSITION_RE = re.compile(
    r"\b(engineer|technician|technologist|manager|specialist|assistant|supervisor|intern|nurse|accountant|sales|doctor|physician|director|analyst|developer|designer|consultant|operator|officer|clerk|coordinator|leader)\b",
    re.IGNORECASE,
)
# 英文职位前常见的资历/技术修饰词：切分「公司 + 职位」时把它们归入职位
_EN_POSITION_MODIFIERS = frozenset(
    {
        "senior", "junior", "sr", "jr", "lead", "staff", "principal", "chief", "head",
        "assistant", "deputy", "vice", "software", "backend", "frontend", "front", "full",
        "stack", "data", "machine", "learning", "ai", "python", "java", "golang", "go",
        "sql", "test", "testing", "automation", "qa", "devops", "cloud", "security",
        "network", "product", "project", "marketing", "operations", "customer", "sales",
        "medical", "laboratory", "clinical", "lab", "quality", "service", "support",
    }
)

# 姓名黑名单：命中任一子串就不是姓名
_NON_NAME_TOKENS: tuple[str, ...] = (
    "简历", "个人", "求职", "应聘", "信息", "电话", "手机", "邮箱", "性别", "年龄",
    "学历", "学位", "专业", "学校", "院校", "毕业", "教育", "工作", "经历", "技能",
    "证书", "评价", "优势", "意向", "期望", "薪资", "城市", "民族", "籍贯", "身高",
    "体重", "政治", "面貌", "医学", "检验", "护理", "临床", "技术", "工程", "计算机",
    "管理", "公司", "医院", "中心", "学院", "大学", "本科", "专科", "硕士", "博士",
    "中专", "高中", "先生", "女士", "招聘", "合适", "声明", "时间", "日期", "专业",
    "姓名", "称", "号码", "编号", "微信", "qq", "邮箱",
)
_EN_NAME_STOP = {
    "resume", "curriculum", "vitae", "personal", "information", "profile", "contact",
    "medical", "laboratory", "education", "experience", "skills", "objective", "summary",
    "phone", "email", "name", "candidate",
}

_NAME_ZH_RE = re.compile(f"^[{_CJK_RANGE}]{{2,4}}$")
_NAME_ZH_DOT_RE = re.compile(f"^[{_CJK_RANGE}]{{1,6}}·[{_CJK_RANGE}]{{1,8}}$")
_NAME_EN_RE = re.compile(
    r"^(?:[A-Z][a-z]+|[A-Z]{2,})(?:\s+(?:[A-Z][a-z]+|[A-Z]{2,})){1,2}$"
)
_NAME_EN_LOOSE_RE = re.compile(r"^[A-Za-z][A-Za-z'\-]+(?:\s+[A-Za-z][A-Za-z'\-]+){1,2}$")

_KNOWN_CITIES: tuple[str, ...] = (
    "北京", "上海", "广州", "深圳", "杭州", "南京", "苏州", "武汉", "成都", "重庆",
    "西安", "天津", "长沙", "郑州", "济南", "青岛", "合肥", "福州", "厦门", "宁波",
    "无锡", "东莞", "佛山", "昆明", "南昌", "贵阳", "南宁", "太原", "石家庄", "沈阳",
    "大连", "长春", "哈尔滨", "兰州", "乌鲁木齐", "十堰", "温州", "绍兴", "嘉兴",
    "金华", "台州", "常州", "南通", "徐州", "扬州", "盐城", "镇江", "泰州", "湖州",
    "丽水", "衢州", "舟山", "呼和浩特", "银川", "西宁", "拉萨", "海口", "三亚",
)

_CN_NUM = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

_YEARS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:累计工作年限|工作经历年限|工作年限|从业年限|经验年限|工作时长|工龄)\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?|[一二三四五六七八九十两]+)\s*年"),
    re.compile(r"([0-9]+(?:\.[0-9]+)?|[一二三四五六七八九十两]+)\s*年(?:以上)?(?:的)?(?:工作|从业|检验|临床|相关|行业)?经[验历]"),
    re.compile(r"(?:从事|拥有|具备|具有|积累了?)\s*([0-9]+|[一二三四五六七八九十两]+)\s*年"),
    re.compile(r"(?:工作|从业)\s*([0-9]+)\s*年"),
)

_PROJECT_PREFIX_RE = re.compile(r"^项目(?!名称)[一二三四五六七八九十0-9]*\s*[：:]?\s*(?P<name>.+)$")
_PROJECT_NAME_KV_RE = re.compile(r"^项目名称\s*[:：]\s*(?P<name>.+)$")


# ---------------------------------------------------------------------------
# 通用文本工具
# ---------------------------------------------------------------------------


def _normalize_text(text: str) -> tuple[str, dict[str, int]]:
    """NFKC + 去 HTML/Markdown 噪声 + 删除 CJK 字符间空格 + OCR 拆字修复。"""
    stats = {"cjk_gaps": 0, "ocr_repairs": 0}
    t = _html.unescape(text or "")
    t = unicodedata.normalize("NFKC", t)
    t = (
        t.replace("\ufeff", "")
        .replace("\u200b", "")
        .replace("\u2060", "")
        .replace("\xa0", " ")
        .replace("\u3000", " ")
    )
    t = _IMG_RE.sub(" ", t)
    t = _HTML_BLOCK_RE.sub("", t)
    t = _BR_RE.sub("\n", t)
    t = _HTML_TAG_RE.sub("", t)
    stats["cjk_gaps"] = len(_CJK_GAP_RE.findall(t))
    t = _CJK_GAP_RE.sub("", t)
    for bad, good in _OCR_REPAIRS:
        n = t.count(bad)
        if n:
            t = t.replace(bad, good)
            stats["ocr_repairs"] += n
    # 文档里的连续省略号 / 破折号会让日期区间解析失败（如 2021..9--2024.7）
    t = re.sub(r"\.{2,}", ".", t)
    t = re.sub(r"[-—–－~～]{2,}", "-", t)
    return t, stats


_PUA_RE = re.compile(r"[\ue000-\uf8ff]")

# 招聘平台导出/页面上的样板文字（不是简历正文）
_BOILERPLATE_RE = re.compile(
    r"^(?:标记【|合适[,，]?立即联系$|合适$|不合适$|立即联系$|声明[:：]|导出时间[:：]|"
    r"ID[:：]\s*\d+$|未经许可|版权所有|以上内容仅)",
)


def _clean_line(line: str) -> str:
    """单行清洗：去 Word 私有区符号 / Markdown 前缀 / 项目符号 / 多余空白。"""
    t = (line or "").replace("\t", " ").strip()
    if not t:
        return ""
    t = _PUA_RE.sub("", t)
    t = _LEAD_MARK_RE.sub("", t)
    t = t.replace("**", "").replace("__", "")
    t = re.sub(r"\s+", " ", t).strip()
    return t


_LABEL_FRAGMENT_HEAD_RE = re.compile(r"^[\u4e00-\u9fff]{1,3}$")
_LABEL_FRAGMENT_TAIL_RE = re.compile(r"^[\u4e00-\u9fff]{1,3}[:：]$")


def _merge_label_fragments(lines: list[str]) -> list[str]:
    """把被 PDF 视觉排版拆开的标签碎片拼回一行。

    BOSS 直聘等平台的 PDF 会把「姓名：」拆成两个 block（``姓`` / ``名：``），
    阅读顺序修正后仍是两行；KV 解析只在同一行见到冒号才认标签，所以这里先
    合并相邻的「1~3 个汉字」+「1~3 个汉字 + 冒号」两行。
    """
    merged: list[str] = []
    index = 0
    total = len(lines)
    while index < total:
        line = lines[index]
        if (
            index + 1 < total
            and _LABEL_FRAGMENT_HEAD_RE.match(line)
            and _LABEL_FRAGMENT_TAIL_RE.match(lines[index + 1])
        ):
            merged.append(line + lines[index + 1])
            index += 2
            continue
        merged.append(line)
        index += 1
    return merged


_TRAILING_LABEL_RE = re.compile(
    r"^(?P<value>.{2,20}?)\s*(?P<label>手机|电话|邮箱|微信|年龄|性别|学历|学位|民族|"
    r"籍贯|身高|政治面貌|现居|现居住地|期望城市|期望薪资|期望职位|求职意向)\s*[:：]\s*$"
)


def _split_trailing_labels(lines: list[str]) -> list[str]:
    """把「值 + 下一个标签」粘在一起的行拆开（BOSS 导出常见）。

    例：``周英手机：`` —— 姓名值 ``周英`` 与下一个字段标签 ``手机：`` 同处一个
    block。拆成两行后，``周英`` 才能作为「姓名：」pending 的值，``手机：`` 也
    才能接住下一行的号码。
    """
    out: list[str] = []
    for line in lines:
        m = _TRAILING_LABEL_RE.match(line)
        if m:
            value = _clean_inline(m.group("value"))
            label = m.group("label")
            plausible = (
                _is_plausible_name(value)
                or bool(re.fullmatch(r"[\u4e00-\u9fff]{2,4}", value))
                or bool(re.fullmatch(r"\d{1,3}岁?", value))
            )
            if value and plausible and not _match_section_header(value):
                out.append(value)
                out.append(f"{label}：")
                continue
        out.append(line)
    return out


def _prepare_lines(normalized: str) -> list[str]:
    out: list[str] = []
    for raw in normalized.splitlines():
        cleaned = _clean_line(raw)
        if cleaned and not _BOILERPLATE_RE.match(cleaned):
            out.append(cleaned)
    return _split_trailing_labels(_merge_label_fragments(out))


def _clean_inline(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"\s+", " ", t)
    return t.strip(" \t:：|｜,，、;；")


def _clean_value(value: str) -> str:
    v = (value or "").strip()
    v = re.sub(r"^[\s:：|｜/／,，、;；·.。\-—–]+", "", v)
    v = re.sub(r"[\s|｜]+$", "", v)
    v = re.sub(r"\s+", " ", v)
    return v.strip()


def _strip_bullet(text: str) -> str:
    t = _clean_line(text)
    t = _LEAD_NUMBER_RE.sub("", t).strip()
    return _clean_inline(t)


def _split_pipe(line: str) -> list[str]:
    return [p.strip() for p in re.split(r"[|｜]", line) if p.strip()]


def _dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        value = _clean_value(item)
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _dedupe_contained(items: Iterable[str]) -> list[str]:
    """去重并丢弃被其它条目包含的短条目（如「英语四级」⊂「大学英语四级」）。"""
    values = _dedupe(items)
    return [
        item
        for item in values
        if not any(other != item and item in other for other in values)
    ]


def _drop_covered_by(items: Iterable[str], covered: Iterable[str]) -> list[str]:
    """丢弃已被另一个列表条目覆盖的项（如 skills 里的「计算机一级证书」与证书字段重复）。

    只有「足够长的条目」才会被判定为覆盖：否则 skills 里的「PCR」「检验」这类
    短词会因子串出现在长证书名里被误删。
    """
    covered_keys = [_norm_key(item) for item in covered]
    out: list[str] = []
    for item in items:
        norm = _norm_key(item)
        if norm and len(norm) >= 4 and any(
            key and len(key) >= 4 and (norm in key or key in norm) for key in covered_keys
        ):
            continue
        out.append(item)
    return out


_KNOWN_TLDS = frozenset(
    {
        "com", "cn", "net", "org", "edu", "gov", "io", "co", "info", "xyz", "top",
        "vip", "cc", "me", "tech", "site", "online", "shop", "work", "ltd", "group",
        "cloud", "ai", "hk", "tw", "mo", "us", "uk", "jp", "kr", "de", "fr", "ru",
    }
)


def _repair_email_domain(email: str) -> str:
    """修复 OCR 在邮箱域名尾部多粘的字母（qq.comd → qq.com）。"""
    local, sep, domain = email.partition("@")
    if not sep or not domain:
        return email
    parts = domain.split(".")
    tld = parts[-1].lower()
    if not tld or tld in _KNOWN_TLDS:
        return email
    for candidate in sorted(_KNOWN_TLDS, key=len, reverse=True):
        if tld.startswith(candidate) and 0 < len(tld) - len(candidate) <= 2:
            parts[-1] = candidate
            return local + "@" + ".".join(parts)
    return email


def _extract_phone(text: str) -> str:
    m = _MOBILE_RE.search(text or "")
    if m:
        return re.sub(r"[ \-]", "", m.group(0))
    return ""


def _extract_landline(text: str) -> str:
    m = _LANDLINE_RE.search(text or "")
    if m:
        return m.group(1).strip()
    return ""


def _extract_email(text: str) -> str:
    m = _EMAIL_RE.search(text or "")
    if not m:
        return ""
    email = m.group(0).strip(" .-")
    if not re.search(r"\.[A-Za-z]{2,}$", email):
        return ""
    return _repair_email_domain(email)


def _extract_gender(value: str) -> str:
    v = (value or "").strip()
    if re.match(r"^(女|女性|female|woman)(?![a-z])", v, re.IGNORECASE):
        return "女"
    if re.match(r"^(男|男性|male|man)(?![a-z])", v, re.IGNORECASE):
        return "男"
    return ""


def _extract_age(value: str) -> Optional[int]:
    text = value or ""
    # 写「1995 年出生」时不能取前两位数字当年龄（会得到 19 岁）。
    if re.search(r"(出生|生于|生日)", text):
        birth = _extract_graduation_year(text)
        if birth:
            age = _today().year - birth
            return age if 14 <= age <= 80 else None
        return None
    m = re.search(r"(\d{1,2})", text)
    if not m:
        return None
    age = int(m.group(1))
    if 14 <= age <= 80:
        return age
    return None


def _extract_graduation_year(value: str) -> Optional[int]:
    for m in _YEAR_RE.finditer(value or ""):
        year = int(m.group(1))
        if 1950 <= year <= 2035:
            return year
    return None


def _cn_to_int(token: str) -> Optional[int]:
    token = (token or "").strip()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    if token in _CN_NUM:
        return _CN_NUM[token]
    if token.startswith("十"):
        rest = _CN_NUM.get(token[1:], 0) if len(token) > 1 else 0
        return 10 + rest
    if token.endswith("十"):
        head = _CN_NUM.get(token[0], 0)
        return head * 10
    if len(token) == 2 and token[0] in _CN_NUM and token[1] in _CN_NUM:
        return _CN_NUM[token[0]] * 10 + _CN_NUM[token[1]]
    return None


# ---------------------------------------------------------------------------
# 日期区间
# ---------------------------------------------------------------------------


def _today() -> _dt.date:
    return _dt.date.today()


def _month_index(date_str: str) -> int:
    return int(date_str[:4]) * 12 + int(date_str[5:7])


def _range_info(match: re.Match[str]) -> dict[str, Any]:
    today = _today()
    sy = int(match.group("sy"))
    sm = match.group("sm")
    if match.group("present"):
        ey, em = today.year, today.month
        present = True
    else:
        ey = int(match.group("ey"))
        em = match.group("em") or "12"
        present = False
    start = f"{sy:04d}-{int(sm or 1):02d}"
    end = f"{ey:04d}-{int(em):02d}"
    return {
        "start": start,
        "end": end,
        "present": present,
        "start_missing": sm is None,
        "end_missing": (not present) and match.group("em") is None,
    }


def _months_between(start: str, end: str, *, start_missing: bool = False, end_missing: bool = False) -> int:
    if not start or not end:
        return 0
    sy, sm = int(start[:4]), int(start[5:7])
    ey, em = int(end[:4]), int(end[5:7])
    # 只有年份（「2019 - 2022」）时不能把缺失月份当成 1 月/12 月，
    # 否则区间会被系统性高估（4 年而非 3 年）。缺月份一律按「同期」保守估计。
    if start_missing and end_missing:
        months = (ey - sy) * 12
    elif start_missing:
        months = (ey - sy) * 12 + em
    elif end_missing:
        months = (ey - sy) * 12 + (sm - 1)
    else:
        months = (ey - sy) * 12 + (em - sm) + 1
    return max(0, months)


# ---------------------------------------------------------------------------
# 段落切分
# ---------------------------------------------------------------------------


def _match_section_header(line: str) -> Optional[str]:
    t = _clean_line(line)
    if not t:
        return None
    t = _LEAD_NUMBER_RE.sub("", t).strip()
    t = t.rstrip(":：").strip()
    if not t:
        return None
    compact = re.sub(r"\s+", "", t).lower()
    if len(t) <= 15 and compact in _ZH_SECTION_INDEX:
        return _ZH_SECTION_INDEX[compact]
    en_key = re.sub(r"\s+", " ", t.lower()).strip()
    if len(t) <= 42 and en_key in _EN_SECTION_INDEX:
        return _EN_SECTION_INDEX[en_key]
    return None


def _split_sections(lines: list[str]) -> tuple[dict[str, list[str]], list[tuple[int, str]]]:
    sections: dict[str, list[str]] = {"preamble": []}
    header_hits: list[tuple[int, str]] = []
    current = "preamble"
    for lineno, line in enumerate(lines, 1):
        section = _match_section_header(line)
        if section:
            current = section
            sections.setdefault(current, [])
            header_hits.append((lineno, section))
            continue
        sections.setdefault(current, []).append(line)
    return sections, header_hits


# ---------------------------------------------------------------------------
# KV（键值对）解析
# ---------------------------------------------------------------------------

_COLON_RE = re.compile(r"^(?P<key>[^:：]{1,24})[:：](?P<value>.*)$")
_WS_KV_RE = re.compile(r"^(?P<key>\S{1,12})\s+(?P<value>.+)$")

# 行内「标签：值标签：值」扫描：Windows OCR 常把多个 KV 挤在一行
# （如「证书：检验师资格证技能：PCR、生化分析仪」），值必须在下一个已知标签处截断。
# 分隔符同时接受全角冒号/半角冒号/句点（OCR 常把冒号识别成「．」，NFKC 后为「.」）。
_LABEL_SEPARATOR_CHARS = " \t.．:：·"


def _label_scan_patterns() -> list[tuple[str, str]]:
    seen: set[str] = set()
    patterns: list[tuple[str, str]] = []
    for field_aliases in _FIELD_ALIASES.values():
        for alias in field_aliases:
            compact = _norm_key(alias)
            if len(compact) < 2 or compact in seen:
                continue
            seen.add(compact)
            parts = [re.escape(part) for part in alias.strip().lower().split()]
            patterns.append((compact, r"\s*".join(parts)))
    patterns.sort(key=lambda item: -len(item[0]))
    return patterns


_LABEL_SCAN_RE = re.compile(
    r"(?P<key>" + "|".join(pattern for _, pattern in _label_scan_patterns()) + r")\s*[:：.．]",
    re.IGNORECASE,
)


def _value_until_label(value: str) -> str:
    """把值截断到下一个已知标签之前（行内多标签场景）。"""
    match = _LABEL_SCAN_RE.search(value)
    if match:
        value = value[: match.start()]
    return value.strip(_LABEL_SEPARATOR_CHARS)


def _label_segments(piece: str) -> list[tuple[str, str]]:
    """把一行拆成若干 (字段, 值)：每个值止于下一个已知标签。"""
    matches = list(_LABEL_SCAN_RE.finditer(piece))
    segments: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        field = _ALIAS_INDEX.get(_norm_key(match.group("key")))
        if not field:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(piece)
        value = piece[match.end(): end].strip(_LABEL_SEPARATOR_CHARS)
        segments.append((field, value))
    return segments


def _school_value_ok(value: str) -> bool:
    v = _clean_value(value)
    if not (2 <= len(v) <= 60):
        return False
    if any(bad in v for bad in ("电话", "邮箱", "@", "简历", "求职")):
        return False
    return True


def _major_value_ok(value: str) -> bool:
    v = _clean_value(value)
    if not (2 <= len(v) <= 40):
        return False
    if re.fullmatch(r"[\d.\-/年月\s]+", v):
        return False
    if any(bad in v for bad in ("简历", "求职意向", "期望")):
        return False
    return True


def _generic_value_ok(value: str) -> bool:
    v = _clean_value(value)
    if not (1 <= len(v) <= 80):
        return False
    if v in ("无", "暂无", "不限", "面议"):
        return v == "面议"
    if any(bad in v for bad in ("简历", "求职意向")):
        return False
    return True


def _city_value_ok(value: str) -> bool:
    v = _clean_value(value)
    return bool(v) and len(v) <= 40 and not any(bad in v for bad in ("简历", "求职意向"))


def _kv_value_ok(field: str, value: str) -> bool:
    v = _clean_value(value)
    if not v:
        return False
    if field == "name":
        return _is_plausible_name(v)
    if field == "gender":
        return bool(_extract_gender(v))
    if field == "age":
        return _extract_age(v) is not None
    if field == "phone":
        return bool(_extract_phone(v) or _extract_landline(v))
    if field == "email":
        return bool(_extract_email(v))
    if field == "education_level":
        return bool(_extract_level_degree(v)[0])
    if field == "degree_title":
        return bool(_extract_level_degree(v)[1])
    if field == "school":
        return _school_value_ok(v)
    if field == "major":
        return _major_value_ok(v)
    if field == "grad_year":
        return _extract_graduation_year(v) is not None
    if field == "work_years":
        return bool(re.search(r"\d|[一二三四五六七八九十两]", v))
    if field == "expected_city":
        return _city_value_ok(v)
    if field == "expected_salary":
        return bool(re.search(r"\d|面议|k|K|万|元|薪", v))
    if field in ("certifications", "skills", "industry"):
        return _generic_value_ok(v) and len(v) <= 300
    return _generic_value_ok(v)


def _colon_parts(piece: str) -> Optional[tuple[str, str]]:
    m = _COLON_RE.match(piece)
    if not m:
        return None
    field = _ALIAS_INDEX.get(_norm_key(m.group("key")))
    if not field:
        return None
    return field, _value_until_label(m.group("value").strip())


def _looks_kv_like(line: str) -> bool:
    for piece in _split_pipe(line):
        if _colon_parts(piece) is not None:
            return True
        if _norm_key(piece.strip(" :：")) in _ALIAS_INDEX:
            return True
        m = _WS_KV_RE.match(piece)
        if m and _norm_key(m.group("key")) in _ALIAS_INDEX:
            return True
    return False


def _match_no_sep(piece: str, store: dict[str, list[str]]) -> bool:
    compact = re.sub(r"\s+", "", piece)
    if not compact:
        return False
    lowered = compact.lower()
    for alias, field in _NO_SEP_ALIASES:
        if not lowered.startswith(alias):
            continue
        rest = compact[len(alias):]
        rest = _clean_value(rest)
        if rest and _kv_value_ok(field, rest):
            store.setdefault(field, []).append(rest)
            return True
    return False


def _collect_kv(lines: list[str]) -> dict[str, list[str]]:
    """全文本收集同义词 KV（含表格 ``键 | 值``、``键 值``、无分隔符 OCR 粘连）。"""
    kv: dict[str, list[str]] = {field: [] for field in _FIELD_ALIASES}
    pending_field: Optional[str] = None
    for line in lines:
        pieces = _split_pipe(line)
        if len(pieces) == 1:
            piece = pieces[0]
            segments = _label_segments(piece)
            if segments:
                pending_field = None
                for field, value in segments:
                    value = _clean_value(value)
                    if value:
                        if _kv_value_ok(field, value):
                            kv[field].append(value)
                    else:
                        pending_field = field
                continue
            bare = _norm_key(piece.strip(" :："))
            field = _ALIAS_INDEX.get(bare)
            if field:
                pending_field = field
                continue
            ws = _WS_KV_RE.match(piece)
            if ws:
                ws_field = _ALIAS_INDEX.get(_norm_key(ws.group("key")))
                ws_value = _clean_value(ws.group("value"))
                if ws_field and _kv_value_ok(ws_field, ws_value):
                    kv[ws_field].append(ws_value)
                    pending_field = None
                    continue
            if _match_no_sep(piece, kv):
                pending_field = None
                continue
            if (
                pending_field
                and len(piece) <= 40
                and not re.search(r"[。；;！？!?]", piece)
                and _kv_value_ok(pending_field, piece)
            ):
                kv[pending_field].append(_clean_value(piece))
            pending_field = None
            continue

        for idx, piece in enumerate(pieces):
            segments = _label_segments(piece)
            if segments:
                for field, value in segments:
                    value = _clean_value(value)
                    if value and _kv_value_ok(field, value):
                        kv[field].append(value)
                continue
            bare = _norm_key(piece.strip(" :："))
            field = _ALIAS_INDEX.get(bare)
            if field:
                if idx + 1 < len(pieces):
                    nxt = pieces[idx + 1]
                    if not _looks_kv_like(nxt) and _kv_value_ok(field, nxt):
                        kv[field].append(_clean_value(nxt))
                continue
            if _match_no_sep(piece, kv):
                continue
        pending_field = None
    return kv


def _first_kv(kv: dict[str, list[str]], field: str) -> str:
    return kv.get(field, [""])[0] if kv.get(field) else ""


# ---------------------------------------------------------------------------
# 学历 / 学位解析
# ---------------------------------------------------------------------------


def _extract_level_degree(text: str) -> tuple[str, str, str]:
    """返回 (level, degree, 去掉学历/学位词后的剩余文本)。"""
    t = (text or "").strip()
    if not t:
        return "", "", ""
    m = re.search(r"\b(doctorate|doctor|phd|master|mba|bachelor|undergraduate|associate)\b", t, re.IGNORECASE)
    if m:
        word = m.group(1).lower()
        if word in ("doctorate", "doctor", "phd"):
            level, degree = "博士", "博士"
        elif word in ("master", "mba"):
            level, degree = "硕士", "硕士"
        elif word in ("bachelor", "undergraduate"):
            level, degree = "本科", "学士"
        else:
            level, degree = "大专", ""
        remainder = (t[: m.start()] + " " + t[m.end():]).strip()
        return level, degree, remainder
    best: Optional[tuple[int, str, str, str]] = None
    for keyword, level, degree in _CN_LEVEL_TABLE:
        idx = t.find(keyword)
        if idx < 0:
            continue
        if best is None or idx < best[0] or (idx == best[0] and len(keyword) > len(best[3])):
            best = (idx, level, degree, keyword)
    if best:
        idx, level, degree, keyword = best
        remainder = (t[:idx] + t[idx + len(keyword):]).strip()
        return level, degree, remainder
    return "", "", t


def _extract_school(text: str) -> tuple[str, str]:
    """从一段文本里拆出 (school, 剩余文本)。"""
    t = _clean_inline(text)
    if not t:
        return "", ""
    if re.search(r"[A-Za-z]{2,}", t):
        if _EN_SCHOOL_RE.search(t):
            return t, ""
        return "", t
    best: Optional[tuple[int, int]] = None
    for keyword in _SCHOOL_KWS_CJK:
        start = 0
        while True:
            idx = t.find(keyword, start)
            if idx < 0:
                break
            end = idx + len(keyword)
            if best is None or end > best[1] or (end == best[1] and idx < best[0]):
                best = (idx, end)
            start = idx + 1
    if not best:
        return "", t
    idx, end = best
    if end == len(t):
        return _clean_inline(t[:end]), ""
    if idx == 0:
        return _clean_inline(t[:end]), _clean_inline(t[end:])
    prefix = t[:idx]
    if len(prefix) <= 8:
        return _clean_inline(t[:end]), _clean_inline(t[end:])
    return _clean_inline(t[idx:end]), _clean_inline(prefix + " " + t[end:])


def _clean_major(text: str) -> str:
    t = _clean_inline(text)
    t = re.sub(r"^专业\s*[：:]?", "", t).strip()
    t = re.sub(r"^(?:of|in)\s+", "", t, flags=re.IGNORECASE).strip()
    t = t.strip(" ,，、;；:：")
    # 去掉 HTML <sub>/<sup> 被删除后残留的孤立拉丁字母（如「医学检验技术f」）
    t = re.sub(r"(?<=[\u4e00-\u9fff])[A-Za-z]$", "", t).strip()
    # 「医学检验技术（大专）」里的学历词被抽走后会残留空括号
    t = re.sub(r"[（(]\s*[)）]", "", t)
    t = re.sub(r"[（(](?:大专|专科|本科|学士|硕士|研究生|博士|高中|中专)[)）]", "", t)
    t = t.strip(" ,，、;；:：")
    t = re.sub(r"(?:专业|方向)$", "", t).strip()
    if not (2 <= len(t) <= 40):
        return ""
    if re.fullmatch(r"[\d.\-/年月\s]+", t):
        return ""
    # 数字占比过高的 token 多为 OCR 噪声/日期（如「2023．的一」）
    if sum(ch.isdigit() for ch in t) * 5 >= len(t) * 2:
        return ""
    if any(kw in t for kw in ("简历", "求职意向", "期望", "课程", "排名", "学分")):
        return ""
    if any(kw in t for kw in ("大学", "学院", "学校")):
        return ""
    return t


_EDU_NOISE_KWS = ("主修课程", "核心课程", "课程", "排名", "学分", "绩点", "GPA")


def _has_school_kw(text: str) -> bool:
    if not text:
        return False
    if _EN_SCHOOL_RE.search(text):
        return True
    return any(kw in text for kw in _SCHOOL_KWS_CJK)


def _starts_with_level(text: str) -> bool:
    t = (text or "").lstrip()
    if not t:
        return False
    return any(t.startswith(keyword) for keyword, _level, _degree in _CN_LEVEL_TABLE)


def _merge_level_degree(entry: dict[str, Any], level: str, degree: str) -> None:
    if level and not entry["level"]:
        entry["level"] = level
    if degree and not entry["degree"]:
        entry["degree"] = degree


def _classify_education_chunk(chunk: str, entry: dict[str, Any]) -> None:
    """把教育段里的一个 token 归类为 学历/学位/学校/专业。

    合并 token（如「赣南医科大学本科医学检验技术f」）先按学校关键词切学校，
    避免「大学本科」把学校名里的「大学」吃掉；以学历词开头的 token 先提学历。
    """
    t = _clean_inline(chunk)
    if not t:
        return
    if _starts_with_level(t):
        level, degree, remainder = _extract_level_degree(t)
        _merge_level_degree(entry, level, degree)
        t = _clean_inline(remainder)
    if t and _has_school_kw(t):
        school, remainder = _extract_school(t)
        if school and school in ("大学", "学院", "学校", "中学", "高中"):
            # 命中的只是学历词的一部分（如「大学本科」）：回退到学历解析
            level, degree, remainder = _extract_level_degree(t)
            _merge_level_degree(entry, level, degree)
            t = _clean_inline(remainder)
        else:
            if school and not entry["school"]:
                entry["school"] = school
            t = _clean_inline(remainder)
    if t:
        level, degree, remainder = _extract_level_degree(t)
        _merge_level_degree(entry, level, degree)
        t = _clean_inline(remainder)
    if not t:
        return
    school, remainder = _extract_school(t)
    if school and not entry["school"]:
        entry["school"] = school
        t = _clean_inline(remainder)
    if t and not entry["major"]:
        major = _clean_major(t)
        if major:
            entry["major"] = major


def _absorb_education_line(line: str, entry: dict[str, Any]) -> None:
    if any(kw in line for kw in _EDU_NOISE_KWS):
        return
    for piece in re.split(r"[|｜]", line):
        piece = _clean_inline(piece)
        if not piece:
            continue
        if re.search(r"[A-Za-z]", piece) and not _CJK_CHAR_RE.search(piece):
            level, degree, remainder = _extract_level_degree(piece)
            _merge_level_degree(entry, level, degree)
            remainder = _clean_inline(remainder)
            if remainder:
                school, rest = _extract_school(remainder)
                if school and not entry["school"]:
                    entry["school"] = school
                if rest and not entry["major"]:
                    major = _clean_major(rest)
                    if major:
                        entry["major"] = major
            continue
        for chunk in re.split(r"[\s,，、;；]+", piece):
            _classify_education_chunk(chunk, entry)


def _new_education_entry() -> dict[str, Any]:
    return {
        "level": "",
        "degree": "",
        "school": "",
        "major": "",
        "graduation_year": None,
        "present": False,
    }


def _parse_education_section(lines: list[str]) -> list[dict[str, Any]]:
    """解析教育段，同时支持两种排版：

    - **条目在前、日期在后**（学校 / 专业 / 日期）：待归类行先缓存，遇到日期
      区间行时收进该条目（``pending`` 机制）；
    - **日期在前、条目在后**（BOSS 直聘等平台导出：日期 / 学校 / 专业）：日期
      行之后若该条目还没有学校/专业，就把紧随其后的行吸收进来，直到下一个
      日期区间行。
    """
    entries: list[dict[str, Any]] = []
    pending: list[str] = []
    total = len(lines)
    index = 0
    while index < total:
        line = lines[index]
        match = _RANGE_RE.search(line)
        if not match:
            pending.append(line)
            index += 1
            continue
        info = _range_info(match)
        entry = _new_education_entry()
        for buffered in pending:
            _absorb_education_line(buffered, entry)
        pending = []
        remainder = _clean_inline(line[: match.start()] + " " + line[match.end():])
        if remainder:
            _absorb_education_line(remainder, entry)
        entry["present"] = info["present"]
        entry["graduation_year"] = None if info["present"] else int(info["end"][:4])
        entries.append(entry)
        if not entry["school"] and not entry["major"]:
            # 日期在前：吸收紧随其后的行，直到下一个日期区间行。
            lookahead = index + 1
            while lookahead < total and not _RANGE_RE.search(lines[lookahead]):
                _absorb_education_line(lines[lookahead], entry)
                lookahead += 1
            if lookahead > index + 1:
                index = lookahead
                continue
        index += 1
    if pending:
        if entries:
            target = entries[-1]
            for buffered in pending:
                _absorb_education_line(buffered, target)
        else:
            entry = _new_education_entry()
            for buffered in pending:
                _absorb_education_line(buffered, entry)
            entries.append(entry)
    return entries


def _choose_education_entry(entries: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not entries:
        return None

    def rank(entry: dict[str, Any]) -> tuple[int, int]:
        return (_LEVEL_RANK.get(entry.get("level", ""), 0), entry.get("graduation_year") or 0)

    return max(entries, key=rank)


# ---------------------------------------------------------------------------
# 工作经历解析
# ---------------------------------------------------------------------------


def _has_company_kw(text: str) -> bool:
    if not text:
        return False
    if _EN_COMPANY_RE.search(text):
        return True
    return any(kw in text for kw in _COMPANY_KWS_CJK)


def _has_position_kw(text: str) -> bool:
    if not text:
        return False
    if _EN_POSITION_RE.search(text):
        return True
    return any(kw in text for kw in _POSITION_KWS_CJK)


def _looks_like_header_line(line: str) -> bool:
    t = (line or "").strip()
    if not t:
        return False
    ascii_only = bool(re.search(r"[A-Za-z]", t)) and not _CJK_CHAR_RE.search(t)
    limit = 90 if ascii_only else 42
    if len(t) > limit:
        return False
    if re.search(r"[。！？!?；;]$", t):
        return False
    if t.endswith(".") and not _EN_COMPANY_SUFFIX_RE.search(t):
        return False
    if _RANGE_RE.search(t):
        return False
    if _LEAD_NUMBER_RE.match(t):
        return False
    if _looks_kv_like(t):
        return False
    return True


def _clean_company_token(token: str) -> str:
    t = _clean_inline(token)
    t = re.sub(r"(?<=[\u4e00-\u9fff])[0-9]+$", "", t)
    t = re.sub(r"\((?:[^()]*)\)$", "", t) if re.fullmatch(r".*\([^()]*\)", t) and len(t) > 12 else t
    return _clean_inline(t)


def _split_cjk_token(token: str) -> tuple[str, str]:
    t = _clean_inline(token)
    if not t:
        return "", ""
    best: Optional[tuple[int, int]] = None
    for keyword in _COMPANY_KWS_CJK:
        start = 0
        while True:
            idx = t.find(keyword, start)
            if idx < 0:
                break
            end = idx + len(keyword)
            if best is None or idx > best[0] or (idx == best[0] and end > best[1]):
                best = (idx, end)
            start = idx + 1
    if best:
        end = best[1]
        rest = t[end:]
        rest_clean = _clean_inline(rest)
        if not rest_clean:
            return _clean_company_token(t), ""
        if re.fullmatch(r"[\(（].*[\)）]", rest_clean):
            return _clean_company_token(t + ""), ""
        if _has_position_kw(rest_clean) and not _has_company_kw(rest_clean):
            return _clean_company_token(t[:end]), _clean_company_token(rest_clean)
        if len(rest_clean) <= 4 and not _has_position_kw(rest_clean):
            return _clean_company_token(t), ""
        return _clean_company_token(t), ""
    if _has_position_kw(t):
        return "", _clean_company_token(t)
    return "", ""


def _split_header_piece(piece: str) -> tuple[str, str]:
    piece = _clean_inline(piece)
    if not piece:
        return "", ""
    has_latin = bool(re.search(r"[A-Za-z]", piece))
    if has_latin and not _CJK_CHAR_RE.search(piece):
        if _EN_COMPANY_SUFFIX_RE.search(piece):
            return _clean_inline(piece), ""
        words = piece.split()
        pos_idx = next(
            (idx for idx, word in enumerate(words) if _EN_POSITION_RE.search(word)), -1
        )
        if pos_idx > 0:
            # 从职位词往前吞掉资历/技术修饰词，剩下的就是公司名
            start = pos_idx
            while start > 0 and words[start - 1].strip(" ,.").lower() in _EN_POSITION_MODIFIERS:
                start -= 1
            if start > 0:
                company = " ".join(words[:start]).strip(" ,.")
                position = " ".join(words[start:]).strip(" ,.")
                return _clean_inline(company), _clean_inline(position)
        if _EN_POSITION_RE.search(piece):
            return "", _clean_inline(piece)
        if _EN_COMPANY_RE.search(piece):
            return _clean_inline(piece), ""
        return _clean_inline(piece), ""
    tokens = [tok for tok in re.split(r"\s+", piece) if tok]
    if len(tokens) <= 1:
        return _split_cjk_token(tokens[0] if tokens else piece)
    company = ""
    position = ""
    leftovers: list[str] = []
    for tok in tokens:
        c, p = _split_cjk_token(tok)
        if c and not company:
            company = c
        elif p and not position:
            position = p
        elif not c and not p:
            leftovers.append(tok)
    for tok in leftovers:
        if not company:
            company = _clean_company_token(tok)
        elif not position:
            position = _clean_company_token(tok)
    return company, position


def _split_company_position(text: str) -> tuple[str, str]:
    """从「公司 / 职位」混杂的标题行里拆出 (company, position)。"""
    text = _clean_inline(text)
    if not text:
        return "", ""
    company = ""
    position = ""
    for piece in re.split(r"[|｜]", text):
        c, p = _split_header_piece(piece)
        if c and not company:
            company = c
        if p and not position:
            position = p
    return company, position


def _is_work_anchor(line: str, match: re.Match[str]) -> bool:
    before = line[: match.start()].strip()
    after = line[match.end():].strip()
    if not before:
        return True
    if re.search(r"[（(]\s*$", before):
        return False  # 括号内嵌日期（如「临检组（2024.07-2025.01）」）
    if _has_company_kw(before) or _has_position_kw(before):
        return True
    if after and (_has_company_kw(after) or _has_position_kw(after)):
        return True
    return False


def _meta_only_line(line: str) -> bool:
    """判断是否是只有键、没有值的元信息行（如「内容：」「公司」），这类行不进 description。"""
    t = _clean_inline(line)
    if not t:
        return True
    if _norm_key(t.strip(" :：")) in _ALIAS_INDEX:
        return True
    colon = _colon_parts(t)
    if colon and not _clean_value(colon[1]):
        return True
    return False


def _parse_experiences(lines: list[str]) -> list[dict[str, Any]]:
    anchors: list[tuple[int, re.Match[str]]] = []
    for idx, line in enumerate(lines):
        match = _RANGE_RE.search(line)
        if match and _is_work_anchor(line, match):
            anchors.append((idx, match))
    if not anchors:
        return []

    # 第一遍：定位每段的企业/职位标题行，并建立全局 consumed 集合，
    # 避免相邻段的标题行同时落进上一段的 description。
    slots: list[dict[str, Any]] = []
    consumed: set[int] = set()
    for pos, (idx, match) in enumerate(anchors):
        block_start = anchors[pos - 1][0] + 1 if pos > 0 else 0
        block_end = anchors[pos + 1][0] if pos + 1 < len(anchors) else len(lines)
        header_idx: list[int] = []
        for j in range(idx - 1, block_start - 1, -1):
            if len(header_idx) >= 2:
                break
            candidate = lines[j]
            if not _looks_like_header_line(candidate):
                break
            c, p = _split_company_position(candidate)
            if not c and not p:
                break
            header_idx.insert(0, j)
            consumed.add(j)
        leading_idx: list[int] = []
        for j in range(idx + 1, min(block_end, idx + 4)):
            if len(leading_idx) >= 3:
                break
            candidate = lines[j]
            if not _looks_like_header_line(candidate):
                break
            c, p = _split_company_position(candidate)
            if not c and not p:
                break
            leading_idx.append(j)
            consumed.add(j)
        slots.append({"idx": idx, "match": match, "header": header_idx, "leading": leading_idx})

    # 第二遍：生成条目与 description
    entries: list[dict[str, Any]] = []
    for pos, slot in enumerate(slots):
        idx = slot["idx"]
        match = slot["match"]
        block_end = slots[pos + 1]["idx"] if pos + 1 < len(slots) else len(lines)
        info = _range_info(match)
        entry: dict[str, Any] = {
            "company": "",
            "position": "",
            "start_date": info["start"],
            "end_date": info["end"],
            "description": "",
            "months": _months_between(
                info["start"],
                info["end"],
                start_missing=info["start_missing"],
                end_missing=info["end_missing"],
            ),
        }
        anchor_line = lines[idx]
        inline = _clean_inline(anchor_line[: match.start()] + " " + anchor_line[match.end():])
        company, position = _split_company_position(inline)
        if company:
            entry["company"] = company
        if position:
            entry["position"] = position
        for j in slot["header"] + slot["leading"]:
            c, p = _split_company_position(lines[j])
            if c and not entry["company"]:
                entry["company"] = c
            if p and not entry["position"]:
                entry["position"] = p
        description_lines = [
            lines[j]
            for j in range(idx + 1, block_end)
            if j not in consumed and not _meta_only_line(lines[j])
        ]
        entry["description"] = "\n".join(description_lines).strip()
        entries.append(entry)
    return entries


def _is_internship(entry: dict[str, Any]) -> bool:
    haystack = f"{entry.get('company', '')} {entry.get('position', '')}"
    return bool(re.search(r"实习|见习|intern|兼职|part[- ]?time", haystack, re.IGNORECASE))


def _entry_recency(entry: dict[str, Any]) -> tuple[str, str]:
    return (entry.get("end_date") or entry.get("start_date") or "", entry.get("start_date") or "")


def _latest_non_intern(entries: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    candidates = [e for e in entries if not _is_internship(e) and (e.get("company") or e.get("position"))]
    if not candidates:
        return None
    return max(candidates, key=_entry_recency)


def _estimate_work_years(entries: list[dict[str, Any]]) -> tuple[int, int]:
    spans: list[tuple[int, int]] = []
    for entry in entries:
        if _is_internship(entry):
            continue
        start = entry.get("start_date") or ""
        end = entry.get("end_date") or ""
        if not start or not end:
            continue
        # 用条目自身的 months（已按缺失月份保守估算）换算成区间长度，
        # 避免只有年份的起止（「2019 - 2022」）被当成完整 12 个月而虚增年限。
        months = int(entry.get("months") or 0)
        if months <= 0:
            continue
        start_idx = _month_index(start)
        spans.append((start_idx, start_idx + months - 1))
    # 逆序/未来日期（数据脏）不能倒扣年限：end < start 的区间直接丢弃。
    spans = [(start, end) for start, end in spans if end >= start]
    if not spans:
        return 0, 0
    spans.sort()
    merged: list[list[int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    months = sum(end - start + 1 for start, end in merged)
    return int(months / 12 + 0.5), months


# ---------------------------------------------------------------------------
# 姓名 / 性别 / 年龄 / 城市
# ---------------------------------------------------------------------------


def _is_plausible_name(value: str) -> bool:
    v = _clean_inline(value)
    if not v or len(v) > 24:
        return False
    if re.search(r"[\d@|｜:：/\\]", v):
        return False
    if any(token in v for token in _NON_NAME_TOKENS):
        return False
    if _NAME_EN_RE.match(v):
        words = {w.lower() for w in v.split()}
        return not (words & _EN_NAME_STOP)
    if _NAME_EN_LOOSE_RE.match(v):
        words = {w.lower() for w in v.split()}
        if words & _EN_NAME_STOP:
            return False
        return True
    if _NAME_ZH_RE.match(v) or _NAME_ZH_DOT_RE.match(v):
        return True
    return False


def _guess_name(preamble: list[str], basic: list[str]) -> str:
    for scope, limit in ((preamble, 4), (basic, 6)):
        for line in scope[:limit]:
            if line.rstrip().endswith((":", "：")):
                continue  # 纯标签行（如「姓名：」）不是姓名值
            candidate = _clean_inline(line)
            if _is_plausible_name(candidate):
                return candidate
    return ""


def _guess_name_from_source(source_name: str) -> str:
    if not source_name:
        return ""
    stem = re.split(r"[\\/]", source_name)[-1]
    stem = re.sub(r"\.[A-Za-z0-9]{1,6}$", "", stem)
    for part in re.split(r"[_\-\s—–·.]+", stem):
        candidate = _clean_inline(part)
        if _is_plausible_name(candidate):
            return candidate
    return ""


def _fallback_gender(lines: list[str]) -> str:
    for line in lines:
        m = re.search(r"性别\s*[:：]?\s*(男|女|male|female)", line, re.IGNORECASE)
        if m:
            return _extract_gender(m.group(1))
        m = re.search(r"(?:^|[\s|｜,，;；])([男女])(?=[\s|｜,，;；]|$)", line)
        if m:
            return m.group(1)
        m = re.search(r"\b(male|female)\b", line, re.IGNORECASE)
        if m:
            return _extract_gender(m.group(1))
    return ""


def _fallback_age(lines: list[str]) -> Optional[int]:
    for line in lines:
        m = re.search(r"年龄\s*[:：]?\s*(\d{1,2})|(\d{1,2})\s*岁", line)
        if not m:
            continue
        age = _extract_age(m.group(1) or m.group(2) or "")
        if age is not None:
            return age
    return None


def _fallback_location(lines: list[str]) -> str:
    for line in lines:
        if re.search(r"期望|意向|求职|应聘", line):
            continue
        m = re.search(r"(?:现居|家住|生活在|坐标)\s*(?:地|城市)?\s*[:：]?\s*([\u4e00-\u9fff]{2,8})", line)
        if m:
            return m.group(1).rstrip("市")
        for chunk in re.split(r"[|｜,，;；/\s]+", line):
            chunk = chunk.strip()
            if not chunk:
                continue
            m = re.fullmatch(f"([{_CJK_RANGE}]{{2,4}})(?:市)?", chunk)
            if not m:
                continue
            city = m.group(1)
            bare = city[:-1] if city.endswith("市") else city
            if city.endswith("市") or bare in _KNOWN_CITIES:
                return bare
    return ""


# ---------------------------------------------------------------------------
# 列表型字段（证书 / 技能 / 行业）
# ---------------------------------------------------------------------------


def _split_multi(value: str, keep_slash: bool = False) -> list[str]:
    pattern = r"[、,，;；|｜]" if keep_slash else r"[、,，;；|｜/／]"
    return [piece for piece in re.split(pattern, value or "") if piece.strip()]


def _list_token_ok(token: str, max_len: int = 40) -> bool:
    t = _strip_bullet(token)
    if not t or len(t) > max_len:
        return False
    if re.fullmatch(r"[\d.\-/年月\s%]+", t):
        return False
    if any(bad in t for bad in ("简历", "求职意向", "期望薪资", "个人信息")):
        return False
    if _norm_key(t) in _ZH_SECTION_INDEX:
        return False
    return True


_CERT_SIGNAL_RE = re.compile(r"证|资格|执照|上岗|级|师|士|照|员")
_CERT_NON_CERT_RE = re.compile(
    r"大赛|比赛|奖学金|优秀|荣誉|称号|志愿者|经历|课程|排名|奖项|奖状|获得|参与|入党|团员|干部|活动"
)
_CERT_LATIN_RE = re.compile(r"(?i)\bcet\b|\bielts\b|\btoefl\b|\bgre\b|\bgmat\b|pmp|cpa|acca")


def _cert_token_ok(token: str) -> bool:
    """证书段常见混入物（获奖、志愿经历、课程描述）不是证书，过滤掉。

    招聘平台常把「获奖情况」「荣誉证书」放在同一段（映射到 certs），也会把
    未识别标题（如「志愿者经历」）的行并进上一段，所以不能只看段落定位。
    """
    t = _strip_bullet(token)
    if not t:
        return False
    if _CERT_NON_CERT_RE.search(t):
        return False
    if _CERT_SIGNAL_RE.search(t) or _CERT_LATIN_RE.search(t):
        return True
    return False


def _clean_cert_token(token: str) -> str:
    """去掉条目编号（``1.`` / ``（2）`` / ``三、``）与残留标点。"""
    t = _strip_bullet(token)
    t = re.sub(r"^(?:[0-9]+|[一二三四五六七八九十]+)\s*[.、)）．]\s*", "", t).strip()
    t = re.sub(r"^[《]|[》]$", "", t).strip()
    # 「获得紧缺人才培训项目(二级实验室骨干人员培训合格证书」这类跨句截断：
    # 括号不配对时只保留最后一个括号之后的内容。
    if t.count("(") > t.count(")"):
        t = t[t.rfind("(") + 1 :].strip()
    return t.strip(" ,，、;；:：")


def _extract_certifications(
    kv: dict[str, list[str]], sections: dict[str, list[str]], normalized: str
) -> list[str]:
    items: list[str] = []
    for raw in kv.get("certifications", []):
        for token in _split_multi(raw, keep_slash=True):
            token = _clean_cert_token(token)
            if _list_token_ok(token) and _cert_token_ok(token):
                items.append(token)
    cert_section = sections.get("certs", [])
    for line in cert_section:
        colon = _colon_parts(line)
        if colon and colon[0] == "skills":
            continue
        text = colon[1] if colon else line
        for token in _split_multi(text, keep_slash=True):
            token = _clean_cert_token(token)
            if _list_token_ok(token) and _cert_token_ok(token):
                items.append(token)
    # 技能段落里可能混入「专业证书：...」这类 KV
    for line in sections.get("skills", []):
        colon = _colon_parts(line)
        if colon and colon[0] == "certifications":
            for token in _split_multi(colon[1], keep_slash=True):
                token = _clean_cert_token(token)
                if _list_token_ok(token) and _cert_token_ok(token):
                    items.append(token)
    for match in re.finditer(
        r"(?:持有|取得|获得|拥有|考取|拿到|已获)\s*[《]?\s*"
        r"([\u4e00-\u9fffA-Za-z（）()》\s]{2,24}?(?:资格证书|资格证|证书|执业证|资格))",
        normalized,
    ):
        token = re.sub(r"\s+", "", match.group(1)).replace("《", "").replace("》", "")
        token = _clean_cert_token(token)
        if _list_token_ok(token) and _cert_token_ok(token):
            items.append(token)
    for match in re.finditer(r"(大学英语[四六]级|英语[四六]级|计算机[一二三四]级|普通话[一二三]级[甲乙等]*|驾驶证)", normalized):
        token = match.group(1)
        if _list_token_ok(token):
            items.append(token)
    return _dedupe_contained(items)


def _clean_skill_token(token: str) -> str:
    t = _strip_bullet(token)
    t = re.sub(r"^(?:如|例如|包括|以及|等)[\s:：]*", "", t).strip()
    return re.sub(r"[。！？!?.;；,，、]+$", "", t).strip()


def _extract_skills(kv: dict[str, list[str]], sections: dict[str, list[str]]) -> list[str]:
    items: list[str] = []
    for raw in kv.get("skills", []):
        for token in _split_multi(raw):
            token = _clean_skill_token(token)
            if _list_token_ok(token, max_len=24):
                items.append(token)
    for line in sections.get("skills", []):
        colon = _colon_parts(line)
        if colon:
            field, value = colon
            if field == "certifications":
                continue
            text = value
        else:
            text = line
            # 未知键（如「计算机:」「语言能力:」）只取冒号后的内容
            m = re.match(r"^[^:：]{1,12}[:：]\s*(.+)$", line)
            if m:
                text = m.group(1)
        for token in _split_multi(text):
            token = _clean_skill_token(token)
            if not _list_token_ok(token, max_len=24):
                continue
            items.append(token)
    return _dedupe_contained(items)


def _extract_industry(kv: dict[str, list[str]]) -> list[str]:
    items: list[str] = []
    for raw in kv.get("industry", []):
        for token in _split_multi(raw, keep_slash=True):
            if _list_token_ok(token, max_len=30):
                items.append(token)
    return _dedupe(items)


# ---------------------------------------------------------------------------
# 项目经验 / 自我评价
# ---------------------------------------------------------------------------


def _parse_projects(lines: list[str]) -> list[dict[str, str]]:
    projects: list[dict[str, str]] = []
    current: Optional[dict[str, str]] = None
    for line in lines:
        text = _strip_bullet(line)
        if not text:
            continue
        match = _PROJECT_NAME_KV_RE.match(text) or _PROJECT_PREFIX_RE.match(text)
        date_match = _RANGE_RE.search(text)
        is_new = False
        name = ""
        if match:
            is_new = True
            name = match.group("name")
        elif current is None:
            is_new = True
            if date_match:
                name = _clean_inline(text[: date_match.start()] + " " + text[date_match.end():])
            else:
                name = text
        elif date_match and current["description"]:
            # 日期行出现在已有项目描述之后：可能是「日期先行」的新项目，也可能是说明文字
            is_new = True
            name = _clean_inline(text[: date_match.start()] + " " + text[date_match.end():])
        if is_new:
            if current and (current["name"] or current["description"]):
                projects.append(current)
            current = {"name": _clean_inline(name)[:80], "description": ""}
        elif current is not None:
            current["description"] = (
                (current["description"] + "\n" + text).strip() if current["description"] else text
            )
    if current and (current["name"] or current["description"]):
        projects.append(current)
    return [project for project in projects if project["name"]]


# ---------------------------------------------------------------------------
# 工作年限
# ---------------------------------------------------------------------------


def _years_from_value(raw: str) -> Optional[int]:
    """把「8」「8年」「5年以上」这类显式年限值转成 int（0..50）。"""
    if not raw:
        return None
    match = re.search(r"([0-9]+(?:\.[0-9]+)?|[一二三四五六七八九十两]+)\s*年?", raw)
    if not match:
        return None
    text = match.group(1)
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", text):
        value: Optional[float] = float(text)
    else:
        converted = _cn_to_int(text)
        value = float(converted) if converted is not None else None
    if value is None or value < 0 or value > 50:
        return None
    return int(value + 0.5)


def _parse_explicit_years(lines: list[str]) -> Optional[int]:
    for line in lines:
        if "实习" in line:
            continue
        for pattern in _YEARS_PATTERNS:
            for match in pattern.finditer(line):
                value = _years_from_value(match.group(1))
                if value is not None:
                    return value
    return None


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def _new_result(raw_text: str) -> dict[str, Any]:
    return {
        "name": "",
        "phone": "",
        "email": "",
        "gender": "",
        "age": None,
        "location": "",
        "education": {"level": "", "degree": "", "school": "", "major": "", "graduation_year": None},
        "total_work_years": 0,
        "current_company": "",
        "current_position": "",
        "expected_position": "",
        "expected_city": "",
        "expected_salary": "",
        "certifications": [],
        "skills": [],
        "industry_tags": [],
        "work_experiences": [],
        "projects": [],
        "self_evaluation": "",
        "raw_text": raw_text,
        "missing_fields": [],
        "extraction_notes": [],
    }


def extract_resume_fields(text: str, *, source_name: str = "") -> dict[str, Any]:
    """把一段简历文本抽成固定键的 dict（键见 :data:`RESUME_FIELD_KEYS`）。

    参数
    ----
    text:
        简历原文（PDF/DOCX 文本层、纯文本或 OCR 结果均可）。
    source_name:
        来源文件名（可选）。仅当正文里完全找不到姓名时，才用文件名前缀兜底，
        且会在 ``extraction_notes`` 里标注「由来源文件名推断」。
    """
    raw_text = text if isinstance(text, str) else ("" if text is None else str(text))
    normalized, norm_stats = _normalize_text(raw_text)
    lines = _prepare_lines(normalized)
    sections, header_hits = _split_sections(lines)
    kv = _collect_kv(lines)
    notes: list[str] = []
    result = _new_result(raw_text)

    if not normalized.strip():
        notes.append("输入为空：未抽取到任何字段")
        result["missing_fields"] = list(_CONTENT_KEYS)
        result["extraction_notes"] = notes
        return result

    if norm_stats["cjk_gaps"]:
        notes.append(f"归一化：删除 {norm_stats['cjk_gaps']} 处 CJK 字符间空格（OCR 输出常见）")
    if norm_stats["ocr_repairs"]:
        notes.append(f"归一化：按常见 OCR 拆字表修复 {norm_stats['ocr_repairs']} 处")

    for lineno, section in header_hits:
        notes.append(f"识别段落：{_SECTION_LABELS.get(section, section)}（第 {lineno} 行）")

    # ---- 姓名 ----
    name = _first_kv(kv, "name")
    if name:
        notes.append("姓名：来自显式字段（姓名/Name）")
    else:
        name = _guess_name(sections.get("preamble", []), sections.get("basic", []))
        if name:
            notes.append("姓名：首行启发式推断（建议人工核对）")
        elif source_name:
            name = _guess_name_from_source(source_name)
            if name:
                notes.append("姓名：由来源文件名推断（建议人工核对）")
    result["name"] = name

    # ---- 联系方式 ----
    result["phone"] = _extract_phone(normalized) or _extract_landline(normalized)
    result["email"] = _extract_email(normalized)

    # ---- 性别 / 年龄 / 现居地 ----
    gender = _extract_gender(_first_kv(kv, "gender"))
    if not gender:
        gender = _fallback_gender(sections.get("preamble", [])[:12] + sections.get("basic", [])[:12])
    result["gender"] = gender

    age_value = _extract_age(_first_kv(kv, "age"))
    if age_value is None:
        age_value = _fallback_age(sections.get("preamble", [])[:12] + sections.get("basic", [])[:12])
    result["age"] = age_value

    location = _first_kv(kv, "location")
    if not location:
        location = _fallback_location(sections.get("preamble", [])[:12] + sections.get("basic", [])[:12])
    result["location"] = _clean_value(location).rstrip("市") if location else ""

    # ---- 教育 ----
    education = result["education"]
    edu_entries = _parse_education_section(sections.get("education", []))
    best_edu = _choose_education_entry(edu_entries)

    kv_level_raw = _first_kv(kv, "education_level")
    kv_level, kv_degree_from_level, _ = (
        _extract_level_degree(kv_level_raw) if kv_level_raw else ("", "", "")
    )
    kv_degree_raw = _first_kv(kv, "degree_title")
    kv_degree_basic, kv_degree_from_title, _ = (
        _extract_level_degree(kv_degree_raw) if kv_degree_raw else ("", "", "")
    )
    if not kv_level and kv_degree_basic:
        kv_level = kv_degree_basic
    kv_degree = kv_degree_from_title or kv_degree_from_level

    entry_level = best_edu["level"] if best_edu else ""
    level = kv_level or entry_level
    if entry_level and _LEVEL_RANK.get(entry_level, 0) > _LEVEL_RANK.get(level, 0):
        level = entry_level
    education["level"] = level

    entry_degree = best_edu["degree"] if best_edu else ""
    education["degree"] = kv_degree or entry_degree

    school = _first_kv(kv, "school") or (best_edu["school"] if best_edu else "")
    education["school"] = _clean_value(school)
    major = _first_kv(kv, "major") or (best_edu["major"] if best_edu else "")
    education["major"] = _clean_major(major) if major else ""
    grad_year = _extract_graduation_year(_first_kv(kv, "grad_year"))
    if grad_year is None and best_edu is not None:
        grad_year = best_edu["graduation_year"]
    education["graduation_year"] = grad_year

    # ---- 工作经历 ----
    work_entries = _parse_experiences(sections.get("work", []))
    work_from_intern = False
    if not work_entries:
        internship_entries = _parse_experiences(sections.get("internship", []))
        if internship_entries:
            work_entries = internship_entries
            work_from_intern = True
            notes.append("工作经历段落为空：用实习经历填充 work_experiences（不计入工作年限、不算在职）")

    latest = None if work_from_intern else _latest_non_intern(work_entries)
    current_company = _first_kv(kv, "current_company") or (latest["company"] if latest else "")
    current_position = _first_kv(kv, "current_position") or (latest["position"] if latest else "")
    if latest is not None:
        if not latest["company"] and current_company:
            latest["company"] = current_company
        if not latest["position"] and current_position:
            latest["position"] = current_position
    result["current_company"] = current_company
    result["current_position"] = current_position
    result["work_experiences"] = work_entries

    # ---- 工作年限 ----
    explicit_years = _parse_explicit_years(lines)
    if explicit_years is None:
        explicit_years = _years_from_value(_first_kv(kv, "work_years"))
    if explicit_years is not None:
        total_years = explicit_years
        notes.append(f"工作年限：采用显式值 {explicit_years} 年")
    elif work_from_intern:
        total_years = 0
        notes.append("工作年限：只有实习经历，按 0 年计（应届生）")
    else:
        total_years, months = _estimate_work_years(work_entries)
        if months:
            notes.append(f"工作年限：按工作经历起止估算（{months} 个月，四舍五入 {total_years} 年）")
    years_unknown = explicit_years is None and (
        work_from_intern
        or not any(entry["months"] > 0 and not _is_internship(entry) for entry in work_entries)
    )
    result["total_work_years"] = total_years

    # ---- 求职意向 ----
    expected_position = _first_kv(kv, "expected_position")
    if not expected_position:
        for line in sections.get("job_intent", [])[:6]:
            if _looks_kv_like(line):
                continue
            candidate = _clean_inline(line)
            if 2 <= len(candidate) <= 40:
                expected_position = candidate
                break
    result["expected_position"] = expected_position

    expected_city = _first_kv(kv, "expected_city")
    if not expected_city:
        for line in sections.get("job_intent", []):
            match = re.search(r"(?:期望地点|期望城市|意向城市|地点|城市)\s*[:：]\s*(.+)", line)
            if match:
                expected_city = _clean_value(match.group(1))
                break
    result["expected_city"] = expected_city
    result["expected_salary"] = _first_kv(kv, "expected_salary")

    # ---- 列表型字段 / 项目 / 自我评价 ----
    result["certifications"] = _extract_certifications(kv, sections, normalized)
    result["skills"] = _drop_covered_by(
        _extract_skills(kv, sections), result["certifications"]
    )
    result["industry_tags"] = _extract_industry(kv)
    result["projects"] = _parse_projects(sections.get("projects", []))
    self_eval_lines = sections.get("self_eval", [])
    result["self_evaluation"] = "\n".join(self_eval_lines).strip()

    # ---- 缺失字段 ----
    missing: list[str] = []
    for key in _CONTENT_KEYS:
        if key == "education":
            if not any(education.values()):
                missing.append("education")
            continue
        if key == "total_work_years":
            if years_unknown:
                missing.append("total_work_years")
            continue
        if not result[key]:
            missing.append(key)
    result["missing_fields"] = missing

    notes.append(f"缺失字段：{', '.join(missing) if missing else '无'}")
    result["extraction_notes"] = notes
    return result


# ---------------------------------------------------------------------------
# 简历粗判（供 CLI 使用）
# ---------------------------------------------------------------------------


def looks_like_resume(text: str) -> bool:
    """粗判一段文本是否像简历（给 CLI 做输入过滤，不追求精确）。"""
    raw = text if isinstance(text, str) else ""
    if len(raw.strip()) < 20:
        return False
    normalized, _stats = _normalize_text(raw)
    lines = _prepare_lines(normalized)
    if not lines:
        return False
    score = 0
    if _MOBILE_RE.search(normalized) or _LANDLINE_RE.search(normalized) or _EMAIL_RE.search(normalized):
        score += 2
    if any(_match_section_header(line) for line in lines):
        score += 2
    if sum(1 for line in lines if _looks_kv_like(line)) >= 2:
        score += 1
    if re.search(r"姓名|性别|年龄|学历|毕业|教育|工作经历|工作经验|求职|应聘|简历", normalized):
        score += 2
    if re.search(r"\b(resume|curriculum vitae|work experience|education|skills)\b", normalized, re.IGNORECASE):
        score += 1
    return score >= 3
