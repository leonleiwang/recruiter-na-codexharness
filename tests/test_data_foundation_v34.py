from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def test_profile_library_is_rebuilt_from_14_original_pdfs():
    library_path = ROOT / 'data/generated/profiles/profile_library.json'
    data = json.loads(library_path.read_text(encoding='utf-8'))
    assert data['meta']['total'] == 14
    assert data['meta']['authority'] == 'VERIFIED_SOURCE_PDF'
    assert len(data['profiles']) == 14

    source_dir = ROOT / 'data/source/profiles/originals'
    pdfs = sorted(source_dir.glob('*.pdf'))
    assert len(pdfs) == 14

    manifest = json.loads((ROOT / 'data/manifests/profile_sources.json').read_text(encoding='utf-8'))
    assert manifest['total_sources'] == 14
    manifest_by_name = {x['file_name']: x for x in manifest['sources']}
    assert set(manifest_by_name) == {p.name for p in pdfs}
    for pdf in pdfs:
        assert manifest_by_name[pdf.name]['sha256'] == sha256(pdf)

    msl = next(x for x in data['profiles'] if x['name'] == '医学联络官')
    assert msl['source_status'] == 'VERIFIED_SOURCE'
    assert msl['source_file'] == '医学联络官.pdf'
    assert msl['source_sha256'] == sha256(source_dir / '医学联络官.pdf')
    ability = next(x for x in msl['key_abilities'] if x['name'] == '问题分析与解决能力')
    # Regression: the old 40-profile library truncated this definition mid-sentence.
    assert '多证据一致方可发布报告' in ability['def']
    assert len(ability['def']) > 60


def test_jd_library_is_reproducible_from_original_excel():
    source = ROOT / 'data/source/jd/岗位清单及JD库.xlsx'
    assert source.exists()
    manifest = json.loads((ROOT / 'data/manifests/jd_source.json').read_text(encoding='utf-8'))
    assert manifest['source_file'] == source.name
    assert manifest['sha256'] == sha256(source)

    data = json.loads((ROOT / 'data/generated/jd/jd_library.json').read_text(encoding='utf-8'))
    assert data['total'] == 45
    assert len(data['entries']) == 45
    assert all(e['source_file'] == source.name for e in data['entries'])
    assert any(e['title'] == '医学信息沟通专员' for e in data['entries'])


def test_resume_source_pack_is_manifested_but_not_runtime_authority():
    manifest = json.loads((ROOT / 'data/manifests/resume_sources.json').read_text(encoding='utf-8'))
    assert manifest['production_authority'] == 'MYSQL'
    assert manifest['candidate_source_files'] == 75
    assert manifest['all_source_files'] == 79
    assert manifest['complete_backup_of_production_mysql'] is False
    assert not (ROOT / 'workspace/resume_db.json').exists()
    assert (ROOT / 'data/dev/resume_db.fixture.json').exists()


def test_runtime_and_agent_do_not_hardcode_library_counts_or_old_paths():
    targets = [
        ROOT / 'AGENTS.md',
        ROOT / 'im/recruiter-na.mjs',
        ROOT / 'runtime/rn/repositories/__init__.py',
    ]
    text = '\n'.join(p.read_text(encoding='utf-8') for p in targets)
    assert '40 standard position profiles' not in text
    assert '45 JD templates' not in text
    assert 'ProfileRepository: standard position profiles (JSON, 40 profiles)' not in text

    active = '\n'.join(
        p.read_text(encoding='utf-8', errors='ignore')
        for base in [ROOT / 'im', ROOT / '.agents/skills']
        for p in base.rglob('*') if p.is_file()
    )
    assert 'workspace/resume_db.json' not in active
