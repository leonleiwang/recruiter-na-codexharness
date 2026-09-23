"""画像 → Candidate Decision 判据一致性守护。

画像库（data/generated/profiles/profile_library.json）是招聘岗位画像的唯一事实来源，
policy 只是它的判据化投影。历史上这里漂移过一次：画像更新后 policy 仍是社招口径，
导致应届候选人被按社招标准成批否决。本文件把"每个画像只按自己的画像条目评估"钉住。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from rn.criteria_adapter_v5 import build_actual_criteria, load_inspection_technician_policy

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "data" / "generated" / "profiles" / "profile_library.json"
SUPPORTED = {"PROFILE-91603AA55F6E": "检验技术员（应届）", "PROFILE-412167F2937C": "检验技术员（社招）"}


def _profiles() -> dict[str, dict]:
    if not LIBRARY.exists():
        pytest.skip("画像库未构建（data/generated/profiles/profile_library.json）")
    data = json.loads(LIBRARY.read_text(encoding="utf-8"))
    rows = data.get("profiles") if isinstance(data, dict) else data
    return {str(p.get("profile_id")): p for p in rows}


def _request(profile_id: str) -> dict:
    return {
        "request_id": "REQ-COVERAGE",
        "position_title": "检验技术员",
        "request": {
            "fields": {"position_title": "检验技术员", "location": "杭州", "extra_hard_requirements": []},
            "profile_match": {"status": "MATCHED", "profile_id": profile_id},
        },
    }


def _adapted_names(profile_id: str) -> set[str]:
    built = build_actual_criteria(_request(profile_id), SimpleNamespace(profile_id=profile_id, name=SUPPORTED[profile_id]))
    return {str(r["name"]) for r in built["criteria"] if r["dimension"] != "request_extra"}


def _profile_item_names(profile: dict) -> set[str]:
    return {
        *(str(x.get("name")) for x in profile.get("key_abilities") or [] if isinstance(x, dict)),
        *(str(x.get("name")) for x in profile.get("key_experiences") or [] if isinstance(x, dict)),
        *(str(x) for x in profile.get("key_traits") or []),
    }


@pytest.mark.parametrize("profile_id", sorted(SUPPORTED))
def test_every_profile_item_exists_as_a_policy_criterion(profile_id: str) -> None:
    """画像里出现的每条能力/经验/特质，都必须在 policy 里有同名判据。

    这是"按最新画像修改"的机械检查：编辑画像后如果忘了同步 policy，
    这条会直接失败，而不是等到筛选结果变差才被发现。
    """
    profile = _profiles().get(profile_id)
    assert profile, f"{profile_id} 不在画像库中"
    all_names = {
        str(x.get("name"))
        for key in ("capabilities", "experiences", "traits")
        for x in (load_inspection_technician_policy().get(key) or [])
    }
    missing = sorted(_profile_item_names(profile) - all_names)
    assert not missing, f"{profile.get('name')} 画像条目未进入 policy 判据：{missing}"


@pytest.mark.parametrize("profile_id", sorted(SUPPORTED))
def test_adapted_criteria_equal_the_profile_items_exactly(profile_id: str) -> None:
    profile = _profiles().get(profile_id)
    assert profile, f"{profile_id} 不在画像库中"
    assert _adapted_names(profile_id) == _profile_item_names(profile)


def test_tag_denominator_follows_the_evaluated_criteria() -> None:
    """分母 = 该画像实际判据数 + 3 个资格标签（学历/资格证/地点）。"""
    policy = load_inspection_technician_policy()
    expected = {"PROFILE-91603AA55F6E": 14, "PROFILE-412167F2937C": 16}
    for profile_id, total in expected.items():
        assert len(_adapted_names(profile_id)) + 3 == total
    # policy 自身是并集，比任一画像都大
    union = sum(len(policy.get(key) or []) for key in ("capabilities", "experiences", "traits"))
    assert union == 16
