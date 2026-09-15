from pathlib import Path


def test_v4_host_tool_exposes_owner_scoped_workflow_branch():
    root = Path(__file__).resolve().parents[1]
    text = (root / 'runtime/rn/host_tool.py').read_text(encoding='utf-8')
    assert 'resource == "workflow"' in text
    assert 'HiringManagerWorkflow' in text
    assert 'HiringRequestRepository' in text


def test_public_im_path_does_not_expose_repository_count_routes():
    root = Path(__file__).resolve().parents[1]
    text = (root / 'im/recruiter-na.mjs').read_text(encoding='utf-8')
    assert 'resume.count' not in text
    assert 'profile.count' not in text
    assert 'jd.count' not in text
    assert "'workflow', 'handle'" in text
