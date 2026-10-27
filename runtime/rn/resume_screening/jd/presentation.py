"""Assemble the「最终呈现」view: 岗位职责 → 任职要求 → 匹配标签.

``jd.md`` stays the publishable two-section document (岗位职责 + 任职要求).
This module builds the single view the screening mode pastes into chat, so the
reply always has exactly three top-level items and the derived tags never leak
into 任职要求. 「加分项」 is folded into 任职要求 as a sub-group for the same
reason — three big sections, nothing else at ``##`` level.
"""

from __future__ import annotations

from typing import Any

from rn.resume_screening.analysis import requirements as requirements_mod
from rn.resume_screening.jd import generator

DUTIES_SECTION = "岗位职责"
REQUIREMENTS_SECTION = "任职要求"
BONUS_SECTION = "加分项"


def _split_sections(document: str) -> tuple[list[str], dict[str, list[str]]]:
    """Split a rendered JD document into its preamble lines and ``## `` sections."""
    preamble: list[str] = []
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in document.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
        elif current is None:
            preamble.append(line)
        else:
            sections[current].append(line)
    return preamble, sections


def _trim(lines: list[str]) -> list[str]:
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _fold_bonus(bonus: list[str]) -> list[str]:
    """Turn the standalone ``## 加分项`` section into a bullet group under 任职要求."""
    folded: list[str] = []
    for line in bonus:
        text = line.strip()
        if not text:
            continue
        if text.startswith("- "):
            text = text[2:].strip()
        if text:
            folded.append(f"  - {text}")
    if not folded:
        return []
    return ["", "- **加分项**：", *folded]


def _tag_name_lines(tags_doc: dict[str, Any]) -> list[str]:
    """List just the capability/experience tag names that actually score the hit rate.

    These are the same rows ``matching`` uses, so the chat deliverable and the score
    can never diverge. The full table (判据方式 / 命中线索 / 特质 / 硬性条件) lives in
    ``jd.tags.md`` and never enters the chat deliverable.
    """
    rows = tags_doc.get("requirements") or []
    lines: list[str] = []
    for dimension, label in (
        (requirements_mod.cues.DIMENSION_CAPABILITY, "能力"),
        (requirements_mod.cues.DIMENSION_EXPERIENCE, "经验"),
    ):
        names: list[str] = []
        for row in rows:
            if row.get("dimension") != dimension:
                continue
            name = requirements_mod.tag_display_name(str(row.get("name") or ""))
            if name and name not in names:
                names.append(name)
        if names:
            lines.append(f"- {label}：" + "、".join(names))
    return lines


def render_presentation(jd: dict[str, Any], *, external: bool = False) -> str:
    """Render the three-section screening presentation for chat delivery."""
    preamble, sections = _split_sections(generator.render_jd_document(jd, external=external))
    duties = _trim(list(sections.get(DUTIES_SECTION, [])))
    requirements = _trim(list(sections.get(REQUIREMENTS_SECTION, [])))
    requirements.extend(_fold_bonus(sections.get(BONUS_SECTION, [])))
    tags_doc = requirements_mod.requirements_from_jd(jd)
    title = str(tags_doc.get("job_name") or jd.get("position_title") or "").strip()
    tag_block = "\n".join([
        f"## 匹配标签（取自 JD：{title}）" if title else "## 匹配标签",
        *_tag_name_lines(tags_doc),
    ])
    blocks = [
        "\n".join(_trim(preamble)),
        "## " + DUTIES_SECTION + "\n" + "\n".join(duties),
        "## " + REQUIREMENTS_SECTION + "\n" + "\n".join(requirements),
        tag_block,
    ]
    return "\n\n".join(block for block in blocks if block.strip()) + "\n"


__all__ = ["render_presentation"]
