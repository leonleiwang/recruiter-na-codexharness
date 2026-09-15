from __future__ import annotations

from rn.repositories import JDRepository, ProfileRepository


def test_profile_repository_reads_only_verified_14_profile_library():
    repo = ProfileRepository()
    assert repo.health().status == 'ok'
    assert repo.count() == 14
    hits = repo.search('医学联络官')
    assert [x.name for x in hits] == ['医学联络官']
    assert hits[0].raw['source_status'] == 'VERIFIED_SOURCE'


def test_jd_repository_exact_and_related_are_separate():
    repo = JDRepository()
    assert repo.count() == 45
    assert repo.search_exact('医学联络官') == []
    exact = repo.search_exact('医学信息沟通专员')
    assert len(exact) == 1
    related = repo.search_related('医学联络官', limit=5)
    assert related
    assert all(x.title != '医学联络官' for x in related)
    assert any(x.title == '医学信息沟通专员' for x in related)
