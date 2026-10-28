"""文本归一化与证据片段工具（analysis 层内部共用）。

简历文本来源不同，形态差异很大：

- PDF 文本层：正常中文，可能带手动换行；
- Word：正常中文；
- Windows OCR：**每个 CJK 字符之间被插入空格**（「姓 名 ： 张 晓 明」），
  偶发形近错字（检测 → 检氵则）；
- 粘贴文本：全角空格、制表符、多个连续空格。

归一化只做「把同一段内容还原成可匹配的形式」，不改字、不做纠错、
不做任何抽取，因此可以安全地被 tags / matching 复用。
"""
from __future__ import annotations

import re
import unicodedata

_CJK = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_CJK_GAP_RE = re.compile(rf"(?<=[{_CJK}])[ \t\u00a0\u3000]+(?=[{_CJK}])")
# OCR 逐字插的是单空格；两个以上空格通常是排版上的字段分隔（「张晓明   学历」），不能删。
_CJK_WIDE_GAP_RE = re.compile(rf"(?<=[{_CJK}]) {{2,}}(?=[{_CJK}])")
_GAP_SENTINEL = "\x00"
# NFKC 会把全角标点折成半角，OCR 又常在「字 标点」之间插空格，两步一起处理。
_CJK_PUNCT = "：:，,。.、；;！!？?（）()【】\\[\\]「」『』〈〉《》…—–-～~"
_SPACE_AROUND_CJK_PUNCT_RE = re.compile(
    rf"(?<=[{_CJK}])[ \t\u00a0\u3000]+(?=[{_CJK_PUNCT}])|(?<=[{_CJK_PUNCT}])[ \t\u00a0\u3000]+(?=[{_CJK}])"
)
_CJK_COLON_RE = re.compile(rf"(?<=[{_CJK}]):")
_MULTI_SPACE_RE = re.compile(r"[ \t\u00a0\u3000]+")

# 繁体/异体字归一到简体：港台简历、繁体 PDF 里「檢驗科／醫學檢驗技術」很常见，
# 词表只维护简体一份，先在归一化阶段把常用字折过来（不改写其它内容）。
_TRADITIONAL_CHARS = (
    "檢驗醫學習練經歷史證書專業團隊協調質細動態務費點標準處溫濕環衛術產統維護養訓講讀寫語課線網數據庫檔體電腦機資訊營銷員師級種單雙復週歲"
)
_SIMPLIFIED_CHARS = (
    "检验医学练习经历史证书专业团队协调质细动态务费点标准处温湿环卫术产统维护养训讲读写语课线网数据库档体电脑机资讯营销员师级种单双复周岁"
)
_TRADITIONAL_MAP = str.maketrans(_TRADITIONAL_CHARS, _SIMPLIFIED_CHARS)


def normalize_text(text: str) -> str:
    """Restore text attacked by OCR spacing / full-width whitespace.

    - 删除 CJK 与 CJK 之间的空白（OCR 逐字空格的主症状）；
    - 删除 CJK 与中文标点之间的空白，并把 CJK 后的半角冒号还原成全角（NFKC 折过的）；
    - 全角空格、制表符、连续空格压成一个半角空格；
    - 统一换行符，去掉行尾空白；
    - 常用繁体/异体字折成简体（词表只维护简体）；
    - 不动 CJK 与 ASCII/数字之间的空格（英文单词、数字不会被打散）。
    """
    value = str(text or "").translate(_TRADITIONAL_MAP)
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = _CJK_WIDE_GAP_RE.sub(_GAP_SENTINEL, value)
    previous = None
    while previous != value:
        previous = value
        value = _CJK_GAP_RE.sub("", value)
    value = _SPACE_AROUND_CJK_PUNCT_RE.sub("", value)
    value = _CJK_COLON_RE.sub("：", value)
    value = value.replace(_GAP_SENTINEL, " ")
    value = "\n".join(_MULTI_SPACE_RE.sub(" ", line).strip() for line in value.split("\n"))
    return value.strip()


def compact_text(text: str) -> str:
    """Normalize and collapse newlines into spaces (single-line search corpus)."""
    return _MULTI_SPACE_RE.sub(" ", normalize_text(text).replace("\n", " ")).strip()


def evidence_snippet(text: str, keyword: str, *, radius: int = 18) -> str:
    """Return a one-line window around the first occurrence of keyword."""
    corpus = compact_text(text)
    if not keyword:
        return ""
    index = corpus.find(keyword)
    if index < 0:
        return ""
    start = max(0, index - radius)
    end = min(len(corpus), index + len(keyword) + radius)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(corpus) else ""
    return f"{prefix}{corpus[start:end]}{suffix}"


def find_keywords(text: str, keywords: list[str] | tuple[str, ...]) -> list[str]:
    """Return the keywords that literally occur in the (normalized) text."""
    corpus = compact_text(text)
    found: list[str] = []
    for keyword in keywords:
        token = str(keyword or "").strip()
        if token and token not in found and token in corpus:
            found.append(token)
    return found


__all__ = ["compact_text", "evidence_snippet", "find_keywords", "normalize_text"]
