from rn.scoring_v4 import combine_component_scores


def test_profile_and_jd_use_40_60_blend_and_outer_30_70():
    result = combine_component_scores(
        rq_score=60,
        profile_score=80,
        jd_score=50,
        profile_matched=True,
        veto=False,
    )
    assert result["profile_weight"] == 0.4
    assert result["jd_weight"] == 0.6
    assert result["jm"] == 62.0
    assert result["cs"] == 61.4


def test_no_profile_uses_jd_100_percent():
    result = combine_component_scores(
        rq_score=70,
        profile_score=None,
        jd_score=80,
        profile_matched=False,
        veto=False,
    )
    assert result["profile_weight"] == 0.0
    assert result["jd_weight"] == 1.0
    assert result["jm"] == 80.0
    assert result["cs"] == 77.0


def test_veto_only_sets_final_cs_negative_one():
    result = combine_component_scores(
        rq_score=70,
        profile_score=90,
        jd_score=90,
        profile_matched=True,
        veto=True,
    )
    assert result["jm"] == 90.0
    assert result["cs"] == -1
