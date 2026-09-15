from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_im_bridge_and_router_identify_v5_product():
    bridge = (ROOT / "im/recruiter-na.mjs").read_text(encoding="utf-8")
    router = (ROOT / "im/router.mjs").read_text(encoding="utf-8")
    assert "Recruiter-Na v5" in bridge
    assert "v5.0.2 DingTalk bridge started" in bridge
    assert "Recruiter-Na v4" not in bridge
    assert "Recruiter-Na v5" in router


def test_deployment_metadata_identifies_v5_product():
    service = (ROOT / "deploy/recruiter-na.service").read_text(encoding="utf-8")
    cloud = (ROOT / "deploy/install-cloud.sh").read_text(encoding="utf-8")
    local = (ROOT / "scripts/install-local.sh").read_text(encoding="utf-8")
    assert "Recruiter-Na v5" in service
    assert "Recruiter-Na v5" in cloud
    assert "Recruiter-Na v5" in local


def test_v502_upgrade_preserves_worker_migration_and_decision_latency_defaults():
    upgrade = (ROOT / "deploy/upgrade-v5.0.2.sh").read_text(encoding="utf-8")
    assert "RECRUITER_DECISION_WORKERS" in upgrade
    assert "RECRUITER_SCORING_WORKERS" in upgrade  # migration input only
    assert "RECRUITER_DECISION_TIMEOUT_SECONDS" in upgrade
    assert "RECRUITER_DECISION_MAX_ATTEMPTS" in upgrade
    assert "RECRUITER_DECISION_MAX_MODELS" in upgrade


def test_v502_upgrade_is_scoped_to_recruiter_na_and_never_names_pulsehr_projects():
    upgrade = (ROOT / "deploy/upgrade-v5.0.2.sh").read_text(encoding="utf-8")
    assert "/opt/recruiter-na" in upgrade
    assert "/opt/pulsehr" not in upgrade.lower()
    assert "pulsehr-personal" not in upgrade.lower()
    assert "pulsehr-testbot" not in upgrade.lower()


def test_v502_upgrade_migrates_generated_deepseek_route_to_qwen_without_overwriting_custom_models():
    upgrade = (ROOT / "deploy/upgrade-v5.0.2.sh").read_text(encoding="utf-8")
    assert "qwen3.6-flash" in upgrade
    assert "qwen3.7-max" in upgrade
    assert 'deepseek-v4-pro|deepseek-v4-flash' in upgrade
    assert "Custom decision models win" in upgrade
