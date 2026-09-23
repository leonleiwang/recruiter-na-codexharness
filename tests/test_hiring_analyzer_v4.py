from rn.hiring_analyzer_v4 import HiringIntentAnalyzer


class P:
    profile_id = "PROFILE-MSL"
    name = "医学联络官"
    raw = {"name": "医学联络官", "description": "医学事务", "key_abilities": [], "key_experiences": []}


def test_missing_llm_config_never_keyword_matches_profile():
    analyzer = HiringIntentAnalyzer(base_url="", model="", api_key="")
    result = analyzer.analyze_initial("我要招一个医学联络官，杭州，1人", [P()])
    assert result["profile_match"]["status"] == "UNAVAILABLE"
    assert result["profile_match"]["profile_id"] == ""
    assert result["fields"] == {}
    assert result["provided_fields"] == []
