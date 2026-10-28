"""PR #1 regressions; synthetic inputs, no tenant libraries or candidate files."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from rn.resume_screening import cli, service
from rn.resume_screening.analysis import fields, matching, requirements, textutil
from rn.resume_screening.jd.generator import build_request_jd, render_jd_document


def _requirements():
    return requirements.requirements_from_jd(build_request_jd('SYNTHETIC', {
        'position_title': '检验技师', 'key_experience_skills': ['检验科经验'],
        'hard_requirements': ['年龄 25-35 岁'],
    }))


def _report(text):
    return service.analyze_text(text, filename='synthetic.txt', requirements_doc=_requirements())


@pytest.mark.parametrize('age', ['30岁', '40岁', ''])
def test_age_does_not_change_recommendation_or_rank(age):
    base = '工作经历\n医院检验科实习\n负责检验科标本处理与报告核对'
    reference = _report('年龄：30岁\n' + base)['matching']
    actual = _report(('年龄：' + age + '\n' if age else '') + base)['matching']
    for key in ['tag_hit_rate', 'overall_percent', 'disqualified', 'recommendation', 'label']:
        assert actual[key] == reference[key]
    assert matching.match_rank_key(actual) == matching.match_rank_key(reference)
    hint = next(h for h in actual['hard_filter_hints'] if h['filter'] == 'age')
    assert hint['blocking'] is False
    assert hint['status'] == '不用于自动决策'
    assert '人工合规审查' in hint['note']
    assert not actual['disqualified']


def test_age_absent_from_requirements_has_identical_rank():
    doc = _requirements()
    no_age = copy.deepcopy(doc)
    no_age['hard_filters'].pop('age', None)
    text = '年龄：40岁\n工作经历\n医院检验科实习\n负责检验科标本处理'
    a = service.analyze_text(text, requirements_doc=doc)['matching']
    b = service.analyze_text(text, requirements_doc=no_age)['matching']
    assert matching.match_rank_key(a) == matching.match_rank_key(b)
    assert a['recommendation'] == b['recommendation']
    assert all('不推荐' not in warning for warning in doc['warnings'])


def test_contradicted_single_and_batch_reports(tmp_path):
    report = _report('自我评价：没有检验科经验')
    items = [i for block in report['matching']['dimensions'].values() for i in block['items']]
    assert any(i['state'] == 'contradicted' for i in items)
    assert '反证' in service.render_report_markdown(report)
    single = service.write_report(report, out_dir=tmp_path / 'single')
    batch = service.write_batch_report({'reports': [report]}, out_dir=tmp_path / 'batch')
    assert '反证' in Path(single['markdown']).read_text(encoding='utf-8')
    assert '反证' in Path(batch['markdown']).read_text(encoding='utf-8')


def test_unknown_state_renders_without_silently_matching():
    report = _report('自我评价：没有检验科经验')
    item = next(i for b in report['matching']['dimensions'].values() for i in b['items'])
    item['state'] = 'future_state'
    item['state_label'] = '未知状态，待核实'
    output = service.render_report_markdown(report)
    assert '未知状态，待核实' in output
    assert '❔' in output


@pytest.mark.parametrize('newline', ['\n', '\r\n', '\r'])
def test_normalization_keeps_course_and_work_lines_separate(newline):
    lines = ['教育背景', '主修课程：临床分子生物学检验、临床检验基础',
             '工作经历', '检验科实习：在检验科完成标本处理与报告核对']
    normalized = textutil.normalize_text(newline.join(lines))
    assert normalized.splitlines() == lines
    filtered = matching._course_free_corpus(normalized)
    assert lines[-1] in filtered
    assert '临床分子生物学' not in filtered
    report = _report(newline.join(lines))
    assert report['matching']['tag_hit_count'] >= 1
    course_only = _report(lines[1])
    assert course_only['matching']['tag_hit_count'] == 0


def test_normalization_preserves_blank_lines_and_punctuation_boundaries():
    assert textutil.normalize_text('工 作 经 历\n\n负责检测。\n检验科实习') == '工作经历\n\n负责检测。\n检验科实习'
    assert textutil.compact_text('工作经历\n检验科实习') == '工作经历 检验科实习'


@pytest.mark.parametrize('start,end,expected', [
    ('2016-12', '2019-12', 37), ('2019-02', '2019-12', 1),
    ('2020-07', '2021-12', 13), ('2020-01', '2021-12', 13),
    ('2022-12', '2021-12', 0),
])
def test_missing_end_month_uses_same_month_estimate(start, end, expected):
    assert fields._months_between(start, end, end_missing=True) == expected


@pytest.mark.parametrize('start,end,flags,expected', [
    ('2016-12', '2019-12', {}, 37),
    ('2019-01', '2022-12', {'start_missing': True, 'end_missing': True}, 36),
    ('2019-01', '2020-12', {'start_missing': True}, 24),
    ('2021-01', '2020-12', {'start_missing': True}, 0),
    ('2020-01', '2020-12', {'start_missing': True, 'end_missing': True}, 0),
])
def test_other_date_conventions_preserved(start, end, flags, expected):
    assert fields._months_between(start, end, **flags) == expected


def test_incomplete_date_keeps_uncertainty_in_extraction():
    result = fields.extract_resume_fields('工作经历\n2016.12-2019 合成检验公司 检验员\n负责标本检测')
    entry = result['work_experiences'][0]
    assert entry['months'] == 37
    assert entry['duration_estimated'] is True
    assert '估算' in ' '.join(result['extraction_notes'])


def _run_jd(tmp_path, capsys, title='检验技师', duties=None, query=None, template_duties='负责合成标本检测与记录'):
    library = tmp_path / 'library.json'
    library.write_text(json.dumps({'total': 1, 'entries': [{
        'jd_id': 'JD-SYNTHETIC', 'title': '检验技师', 'source_status': 'VERIFIED_SOURCE',
        'core_responsibilities': template_duties, 'requirements_text': '未经确认的模板条件',
    }]}, ensure_ascii=False), encoding='utf-8')
    input_file = tmp_path / 'fields.json'
    input_file.write_text(json.dumps({'position_title': title, 'core_responsibilities': duties or []}, ensure_ascii=False), encoding='utf-8')
    args = ['jd', '--fields-file', str(input_file), '--library-file', str(library), '--json', '--no-write']
    if query:
        args += ['--related-jd-title', query]
    assert cli.main(args) == 0
    return json.loads(capsys.readouterr().out)


@pytest.mark.parametrize('query', [None, '检验技师'])
def test_exact_template_duties_are_unconfirmed_reference(tmp_path, capsys, query):
    jd = _run_jd(tmp_path, capsys, query=query)
    assert jd['responsibilities'] == []
    assert jd['related_template']['jd_id'] == 'JD-SYNTHETIC'
    assert jd['related_template']['match_type'] == 'exact'
    assert jd['related_template']['reference']['responsibilities']
    output = render_jd_document(jd, external=True)
    assert '负责合成标本检测与记录' in output
    assert '发布前需用人部门确认' in output
    req = requirements.requirements_from_jd(jd)
    assert not req['requirements']
    assert not req['hard_filters']


def test_template_cannot_override_user_duties(tmp_path, capsys):
    jd = _run_jd(tmp_path, capsys, duties=['用户确认职责'])
    assert jd['responsibilities'] == ['用户确认职责']
    output = render_jd_document(jd, external=True)
    assert '用户确认职责' in output
    assert '负责合成标本检测与记录' not in output


def test_related_template_stays_reference(tmp_path, capsys):
    jd = _run_jd(tmp_path, capsys, title='检验技术员')
    assert jd['related_template']['match_type'] == 'related'
    assert jd['related_template']['reference']['responsibilities']
    assert jd['responsibilities'] == []


def test_unrelated_title_has_no_template(tmp_path, capsys):
    jd = _run_jd(tmp_path, capsys, title='完全无关的岗位XYZ')
    assert jd['related_template']['match_type'] == 'none'


def test_exact_template_without_duties_does_not_fabricate(tmp_path, capsys):
    jd = _run_jd(tmp_path, capsys, template_duties='')
    assert jd['responsibilities'] == []
    assert '补充 3–5 条' in render_jd_document(jd)


def test_age_requirements_markdown_has_no_rejection_instruction():
    output = requirements.render_requirements_markdown(_requirements())
    age_row = next(line for line in output.splitlines() if '| 年龄 |' in line)
    assert '不推荐' not in age_row
    assert '人工合规审查' in age_row


def test_estimated_dates_are_marked_in_user_report():
    report = _report('工作经历\n2016.12-2019 合成检验公司 检验员\n负责标本检测')
    output = service.render_report_markdown(report)
    assert '月份不完整，时长估算' in output


@pytest.mark.parametrize('kind', ['docx', 'pdf'])
def test_synthetic_document_line_boundaries_survive_ingestion(kind):
    import io
    from rn.resume_screening.ingest.document import load_bytes
    lines = ['教育背景', '主修课程：临床分子生物学检验', '工作经历', '检验科实习负责标本检测']
    if kind == 'docx':
        from docx import Document
        document = Document()
        for line in lines:
            document.add_paragraph(line)
        stream = io.BytesIO()
        document.save(stream)
        data = stream.getvalue()
    else:
        import fitz
        document = fitz.open()
        page = document.new_page()
        for i, line in enumerate(lines):
            page.insert_text((72, 72 + 24 * i), line, fontname='china-s')
        data = document.tobytes()
        document.close()
    extracted = load_bytes(data, f'synthetic.{kind}')
    normalized = textutil.normalize_text(extracted.text)
    assert '检验科实习负责标本检测' in matching._course_free_corpus(normalized)
    assert '临床分子生物学' not in matching._course_free_corpus(normalized)


def test_cli_jd_to_single_and_batch_reports(tmp_path, capsys):
    jd = _run_jd(tmp_path, capsys)
    # Only user-confirmed skills enter analysis; reference duties stay separate.
    jd['key_experience_skills'] = ['检验科经验']
    jd['request_key_experience_skills'] = ['检验科经验']
    jd_path = tmp_path / 'jd.json'
    jd_path.write_text(json.dumps(jd, ensure_ascii=False), encoding='utf-8')
    negative = tmp_path / 'negative.txt'
    positive = tmp_path / 'positive.txt'
    negative.write_text('自我评价：没有检验科经验', encoding='utf-8')
    positive.write_text('教育背景\n主修课程：临床检验基础\n工作经历\n医院检验科实习负责标本处理', encoding='utf-8')
    for inputs, folder in [([str(negative)], 'single'), ([str(negative), str(positive)], 'batch')]:
        out = tmp_path / folder
        assert cli.main(['analyze', *inputs, '--jd-json', str(jd_path), '--out', str(out), '--json']) == 0
        capsys.readouterr()
        markdown_files = list(out.glob('*.md'))
        assert markdown_files
        assert any('反证' in file.read_text(encoding='utf-8') for file in markdown_files)
    batch = json.loads((tmp_path / 'batch' / 'batch.json').read_text(encoding='utf-8'))
    assert batch['ranking'][0]['filename'] == 'positive.txt'
