from pathlib import Path


def test_active_runtime_and_skills_have_no_v22_scorer_or_silent_keyword_fallback():
    root = Path(__file__).resolve().parents[1]
    needles = ("scoring_v22", "evaluate_hr_skill_v22", "_match_tag", "hr-skill-v2.2", "简历筛选规则_v2.2")
    violations = []
    for base in (root / "runtime", root / "skills"):
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".md", ".json", ".yml", ".yaml"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for needle in needles:
                if needle in text:
                    violations.append(f"{path.relative_to(root)}: {needle}")
    assert not violations, "legacy scoring remains active:\n" + "\n".join(violations)
