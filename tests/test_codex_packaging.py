from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SKILLS = {
    "hiring-request",
    "my-hiring-progress",
}

def test_codex_repo_skills_live_in_documented_agents_root():
    root = ROOT / ".agents" / "skills"
    found = {p.parent.name for p in root.glob("*/SKILL.md")} if root.exists() else set()
    assert found == EXPECTED_SKILLS


def test_no_duplicate_top_level_active_skills_tree():
    assert not (ROOT / "skills").exists()


def test_all_skill_frontmatter_names_match_directory_names():
    root = ROOT / ".agents" / "skills"
    for skill_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        assert text.startswith("---\n")
        head = text.split("---", 2)[1]
        assert f"name: {skill_dir.name}" in head
