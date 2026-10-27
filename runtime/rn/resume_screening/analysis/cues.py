"""三维度线索词表：能力 / 经验 / 特质。

这张表是「简历 → 标签」与「岗位 → 要求」两侧共用的唯一词表，保证双边用同一套
语义口径对齐（否则匹配会因两套同义词表而漏命中）。

约定：
- 每个条目 = (dimension, canonical_name, keywords)；
- ``keywords`` 里以 ``!`` 开头的词视为「强线索」（出现即可判定已证实，如「检验科」
  「带教」「质控失控」），其余为弱线索（需要多条共现或上下文佐证）；
- 词表只表达「简历里哪些文字支持这条要求」，不表达权重与决策——权重与判定
  在 ``matching.py`` / v5 决策链路里各自负责。

新增判据时：先在政策 JSON 里加条目，再在这里补同名 canonical entry，
并同步 ``tests/test_resume_screening_analysis.py`` 的维度数量断言。
"""
from __future__ import annotations

from dataclasses import dataclass, field

DIMENSION_CAPABILITY = "capability"
DIMENSION_EXPERIENCE = "experience"
DIMENSION_TRAIT = "trait"
DIMENSIONS: tuple[str, ...] = (DIMENSION_CAPABILITY, DIMENSION_EXPERIENCE, DIMENSION_TRAIT)
# 参与匹配度加权（打分）的维度：特质（抗压/细心/责任心等）主观性强、简历自述易失真，
# 只做参考展示，不纳入打分。简历侧仍然抽取特质标签，岗位侧仍然列特质要求，方便面试关注。
SCORING_DIMENSIONS: tuple[str, ...] = (DIMENSION_CAPABILITY, DIMENSION_EXPERIENCE)
REFERENCE_DIMENSIONS: tuple[str, ...] = (DIMENSION_TRAIT,)
DIMENSION_LABELS: dict[str, str] = {
    DIMENSION_CAPABILITY: "能力",
    DIMENSION_EXPERIENCE: "经验",
    DIMENSION_TRAIT: "特质",
}


@dataclass(frozen=True)
class Cue:
    dimension: str
    name: str
    keywords: tuple[str, ...] = field(default_factory=tuple)

    @property
    def strong_keywords(self) -> tuple[str, ...]:
        return tuple(kw[1:] for kw in self.keywords if kw.startswith("!"))

    @property
    def weak_keywords(self) -> tuple[str, ...]:
        return tuple(kw for kw in self.keywords if kw[:1] not in ("!", "?"))

    @property
    def weak_only_keywords(self) -> tuple[str, ...]:
        """「?」前缀：只能算待核实的泛泛表述，永远不能单独/叠加升级成命中。

        例如「积极参加学习培训活动」「学习适应能力较好」只说明态度，不能和
        「GPA 4.1」「成绩排名前 5%」这类硬证据同等看待。
        """
        return tuple(kw[1:] for kw in self.keywords if kw.startswith("?"))

    def all_keywords(self) -> tuple[str, ...]:
        return tuple(kw[1:] if kw[:1] in ("!", "?") else kw for kw in self.keywords)


CUES: tuple[Cue, ...] = (
    # ---------------- 能力（前 7 条与政策 JSON 的 capabilities 同名） ----------------
    Cue(DIMENSION_CAPABILITY, "规范操作与执行能力", (
        # 政策 evidence_hints：SOP/操作规程/标准化操作/操作规范 + 实验室/检验科工作经历
        "!SOP", "!标准操作规程", "!操作规程", "!室内质控", "!室间质评", "!上机检测", "!结果审核",
        "!标本接收", "!标准化操作", "!质控规则", "!质控结果", "!ISO15189", "!ISO 15189", "!CNAS", "!CLIA",
        "操作规范", "规范化", "按规程", "严格执行", "操作流程",
        "质控", "审核", "复核", "检测流程", "上机", "实验室", "检验科", "仪器操作",
    )),
    Cue(DIMENSION_CAPABILITY, "问题分析与解决能力", (
        "!根因分析", "!异常处理", "!质控失控", "!失控处理", "!异常案例", "!结果异常追溯", "!CAPA", "!PDCA",
        "问题分析", "问题排查", "排查", "结果异常", "异常结果", "投诉处理", "追溯",
        "原因分析", "独立处置", "复盘异常", "分析原因",
    )),
    Cue(DIMENSION_CAPABILITY, "快速学习能力", (
        # 命中只认「被验证过」的证据：排名前 15%（阈值在 matching 里判定）或培训合格证书/奖学金。
        # GPA/绩点的具体数值不看（学校难度不可比），只作为线索；「我学习能力强」是自我陈述，面试再验证。
        "!培训合格证书", "!奖学金",
        "?成绩排名", "?专业排名", "?排名前", "?GPA", "?绩点",
        "?快速学习", "?学习能力", "?上手快", "?快速上手", "?短期内掌握", "?多项目学习",
        "?举一反三", "?自学", "?接受新事物",
        # 只写了「参加学习培训/学习适应能力」这类泛泛表述：够不上命中，给待核实。
        "?学习培训", "?培训活动", "?学习适应", "?继续学习", "?理论学习", "?快速适应",
    )),
    Cue(DIMENSION_CAPABILITY, "沟通表达能力", (
        "!沟通协调", "!跨部门", "沟通", "表达", "交接", "汇报", "传达", "协调",
    )),
    Cue(DIMENSION_CAPABILITY, "团队合作能力", (
        # 社团/学生会/志愿服务属团队经历；「协助检验医师」「配合完成」是协作行为（人工校验口径）
        "!团队协作", "!团队合作", "!社团", "!社团活动", "!学生会", "!志愿服务", "!协助检验医师",
        "!配合完成", "!团队意识", "团队", "协作", "配合", "互助", "排班",
    )),
    Cue(DIMENSION_CAPABILITY, "试剂耗材管理能力", (
        "!试剂管理", "!效期管理", "!库存核对", "!试剂更换验证", "!新批次质控比对",
        "试剂更换", "定标验证", "试剂领用", "批号比对", "试剂", "耗材", "定标", "领用",
        "库存", "批号",
    )),
    Cue(DIMENSION_CAPABILITY, "专业理论知识", (
        "!检验学", "!免疫学", "!生物化学", "专业理论", "课程成绩", "理论", "原理", "课程",
        "专业知识", "临检", "生化", "免疫",
    )),
    # 以下两条是简历侧的精细能力线索（政策未单列，用于让简历标签更具体；
    # 岗位侧使用 JD 文本时也能把它们归成要求）。
    Cue(DIMENSION_CAPABILITY, "仪器操作能力", (
        "!生化分析仪", "!血球仪", "!尿沉渣", "!PCR", "!化学发光", "!检验仪器", "!仪器设备",
        "!LIS", "!HIS", "!LIS系统", "!HIS系统", "!条码打印", "!仪器保养", "!仪器维护",
        "!上机操作", "仪器", "设备操作", "上机",
    )),
    Cue(DIMENSION_CAPABILITY, "样本检测能力", (
        # 样本/标本的处理、接收、前处理都是检验岗的日常动作，单凭其一即可认定（人工校验口径）
        "!标本接收", "!样本检测", "!临检标本", "!标本", "!样本", "!样本前处理", "!标本前处理",
        "!样本处理", "!标本处理", "!样本接收", "!标本制备", "!样品前处理", "!样品", "!实验前处理",
        "条码", "结果审核", "报告发布",
    )),
    # ---------------- 经验（前 4 条与政策 experiences 同名） ----------------
    Cue(DIMENSION_EXPERIENCE, "检验科经验", (
        "!检验科", "!医学检验所", "!第三方检验", "!ICL", "!迪安", "!金域", "!艾迪康",
        "临检", "检验工作", "医院检验",
    )),
    Cue(DIMENSION_EXPERIENCE, "试剂管理经验", (
        "!试剂管理", "!试剂更换", "!效期管理", "库存核对", "定标", "试剂领用", "批号比对",
    )),
    Cue(DIMENSION_EXPERIENCE, "人员带教经验", (
        "!带教", "!指导新人", "指导实习生", "培训", "实习生", "新人培养", "师徒",
    )),
    Cue(DIMENSION_EXPERIENCE, "专业背景与项目经验", (
        "!医学检验技术", "!临床医学", "!生物技术", "课题", "毕业项目", "课程实验", "科研项目",
    )),
    # 简历侧精细线索
    Cue(DIMENSION_EXPERIENCE, "大样本量经验", (
        "!大样本量", "!大批量", "!体检季", "!日样本量", "高峰", "样本量激增", "高强度",
    )),
    Cue(DIMENSION_EXPERIENCE, "分子或微生物检测经验", (
        "!分子生物学", "!分子检测", "!分子诊断", "!微生物检测", "!微生物培养", "!微生物",
        "!分子", "!PCR", "!微生物检验", "!分子检验", "!微生物上岗证", "!核酸提取",
        "分子项目", "微生物项目",
    )),
    # ---------------- 特质（与政策 traits 同名） ----------------
    Cue(DIMENSION_TRAIT, "抗压", (
        "!抗压", "!压力", "!高强度", "加班", "夜班", "急诊", "高峰", "连续作战",
    )),
    Cue(DIMENSION_TRAIT, "细心", (
        "!细心", "!严谨", "!仔细", "核对", "复核", "审核", "差错", "认真",
    )),
    Cue(DIMENSION_TRAIT, "责任心", (
        # 「负责」是职责句高频动词（负责结果审核/负责设备保养），不能当性格自述，
        # 否则任何职责描述都会被判成「责任心」；只保留明确的自我评价表述。
        "!责任心", "!尽职", "!担当", "不推诿", "主动承担", "工作负责",
    )),
    Cue(DIMENSION_TRAIT, "积极主动", (
        "!积极主动", "!主动承担", "改进建议", "流程优化", "额外职责", "自驱", "主动",
    )),
    Cue(DIMENSION_TRAIT, "持续学习", (
        "!持续学习", "!主动学习", "复盘", "总结经验", "钻研", "自我提升", "记录要点",
    )),
)

CUES_BY_DIMENSION: dict[str, tuple[Cue, ...]] = {
    dim: tuple(cue for cue in CUES if cue.dimension == dim) for dim in DIMENSIONS
}
CUES_BY_NAME: dict[tuple[str, str], Cue] = {(cue.dimension, cue.name): cue for cue in CUES}


def find_cue(dimension: str, name: str) -> Cue | None:
    """Look up a cue entry by (dimension, canonical name)."""
    return CUES_BY_NAME.get((str(dimension or "").strip(), str(name or "").strip()))


def match_cues(text: str, *, dimensions: tuple[str, ...] = DIMENSIONS) -> list[dict]:
    """Scan text for every cue entry; return hits with the keywords that fired.

    Result rows: ``{"dimension", "name", "keywords": [fired...], "strong": [fired strong...],
    "score": len(strong)*2 + len(weak)}``。调用方自己决定阈值。
    """
    haystack = str(text or "")
    if not haystack:
        return []
    hits: list[dict] = []
    for dim in dimensions:
        for cue in CUES_BY_DIMENSION.get(dim, ()):
            fired: list[str] = []
            strong: list[str] = []
            for kw in cue.all_keywords():
                if kw and kw in haystack:
                    fired.append(kw)
                    if kw in cue.strong_keywords:
                        strong.append(kw)
            if fired:
                hits.append({
                    "dimension": cue.dimension,
                    "name": cue.name,
                    "keywords": fired,
                    "strong": strong,
                    "weak_only": [kw for kw in fired if kw in cue.weak_only_keywords],
                    "score": len(strong) * 2 + (len(fired) - len(strong)),
                })
    return hits


def canonicalize(text: str, *, dimension: str | None = None) -> str:
    """Map free job-description text onto a canonical cue name when possible.

    返回命中的 canonical name（按 score 取最高，若给 dimension 则限定维度），
    没有命中返回空串。
    """
    dims = (dimension,) if dimension else DIMENSIONS
    hits = [row for row in match_cues(text, dimensions=dims) if row["score"] > 0]
    if not hits:
        return ""
    hits.sort(key=lambda row: (-row["score"], row["name"]))
    return hits[0]["name"]


__all__ = [
    "Cue",
    "CUES",
    "CUES_BY_DIMENSION",
    "DIMENSIONS",
    "DIMENSION_LABELS",
    "DIMENSION_CAPABILITY",
    "DIMENSION_EXPERIENCE",
    "DIMENSION_TRAIT",
    "canonicalize",
    "find_cue",
    "match_cues",
]
