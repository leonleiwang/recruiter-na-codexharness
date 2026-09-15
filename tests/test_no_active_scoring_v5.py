from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ACTIVE_FILES = [
    "runtime/rn/host_tool.py",
    "runtime/rn/hiring_workflow_v5.py",
    "runtime/rn/screening_v5.py",
    "runtime/rn/candidate_decision_v5.py",
    "runtime/rn/criteria_adapter_v5.py",
    "runtime/rn/decision_evidence_evaluator.py",
    ".agents/skills/hiring-request/SKILL.md",
    "README.md",
    "AGENTS.md",
    "USAGE.md",
]


def test_release_version_and_v5_upgrade_script_exist():
    assert (ROOT / "VERSION").read_text(encoding="utf-8").strip() == "5.0.2"
    assert (ROOT / "deploy/upgrade-v5.0.2.sh").exists()


def test_host_tool_uses_v5_workflow_and_decision_screening():
    text = (ROOT / "runtime/rn/host_tool.py").read_text(encoding="utf-8")
    assert "from rn.hiring_workflow_v5 import HiringManagerWorkflow" in text
    assert "from rn.screening_v5 import HiringDecisionScreeningService" in text
    assert "HiringDecisionScreeningService(" in text
    assert "HiringScreeningService" not in text
    assert "from rn.screening_v4" not in text


def test_active_v5_path_has_no_formal_candidate_score_contract_or_imports():
    forbidden_imports = ("scoring_v4", "scoring_engine_v3", "screening_v4", "screening_pipeline_v3")
    formula_needles = ("ProfileMatch ×", "JDMatch ×", "RQ ×", "JM =", "CS =")
    violations = []
    for rel in ACTIVE_FILES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for needle in forbidden_imports + formula_needles:
            if needle in text:
                violations.append(f"{rel}: {needle}")
    assert not violations, "active score path remains:\n" + "\n".join(violations)


def test_hiring_manager_skill_describes_candidate_decision_not_numeric_scoring():
    text = (ROOT / ".agents/skills/hiring-request/SKILL.md").read_text(encoding="utf-8")
    assert "Recruiter-Na v5" in text
    assert "Candidate Decision" in text
    assert "UNKNOWN" in text
    assert "hard_gate" in text  # internal rule may be documented to Codex, not exposed to user
    assert "ProfileMatch 40%" not in text
    assert "RQ 30%" not in text
    assert "正式候选人评分" not in text


def test_readme_marks_old_scorers_as_legacy_non_active():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert text.startswith("# Recruiter-Na v5.0.2")
    assert "Candidate Decision" in text
    assert "legacy" in text.lower()
    assert "screening_v5.py" in text
    assert "scoring_v4.py               # deterministic" not in text


def test_v5_workflow_uses_recommendation_snapshot_api_not_legacy_ranking_method():
    text = (ROOT / "runtime/rn/hiring_workflow_v5.py").read_text(encoding="utf-8")
    assert "set_recommendations_for_owner" in text
    assert "set_ranking_for_owner" not in text
