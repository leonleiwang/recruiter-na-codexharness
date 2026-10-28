"""Goal 5: synthetic wrapped courses must not become work experience."""
import io

import pytest

from rn.resume_screening import service
from rn.resume_screening.analysis import matching, requirements, textutil


def _report(text, job='分子或微生物检测经验'):
    return service.analyze_text(text, requirements_doc=requirements.requirements_from_jd_text(job))


def _state(report):
    return report['matching']['dimensions']['experience']['items'][0]['state']


@pytest.mark.parametrize('courses', [
    '主修课程：\n临床分子生物学检验\n临床检验基础',
    '主修课程：临床检验基础\n临床分子生物学检验',
    '主修课程：\n  • 临床检验基础\n  • 临床分子生物学检验',
    '专业课程：\n1. 临床检验基础\n2、临床分子生物学检验',
    '所学课程：\n- 生物化学\n- 免疫学检验\n- 临床检验基础\n- 分子生物学\n- PCR',
    '主修课程：\n生物化学\n\n临床分子生物学检验',
])
def test_course_continuations_cannot_supply_experience(courses):
    text = '教育背景\n' + courses
    filtered = matching._course_free_corpus(textutil.normalize_text(text))
    assert '分子生物学' not in filtered
    assert 'PCR' not in filtered
    report = _report(text)
    assert _state(report) == 'not_found'
    assert report['matching']['tag_hit_rate'] == 0
    assert report['matching']['recommendation'] == '待核实'
    clean = _report('教育背景\n主修课程：临床检验基础')
    assert matching.match_rank_key(report['matching']) == matching.match_rank_key(clean['matching'])


@pytest.mark.parametrize('boundary', [
    '工作经历', '实习经历', '项目经历', '工作经历：合成医院',
    '一、工作经历', '2. 实习经历', '工作经验', '项目经验',
])
def test_explicit_sections_end_course_context(boundary):
    work = '负责分子检测和样本处理'
    text = f'教育背景\n主修课程：\n临床分子生物学检验\n\n{boundary}\n{work}'
    filtered = matching._course_free_corpus(textutil.normalize_text(text))
    assert '临床分子生物学检验' not in filtered
    assert textutil.normalize_text(work) in filtered
    assert _state(_report(text)) == 'matched'


@pytest.mark.parametrize('work', [
    '检验科实习：负责样本处理和报告核对',
    '负责分子检测与报告核对',
    '2021.07-2024.08 合成医院 检验员',
    '在医院检验科参与分子检测',
    '曾在实验室完成微生物检测',
    '合成医院 检验员',
    '这是一段没有标题的个人叙述。',
    '从事分子检测',
    '使用PCR技术进行分子检测',
    '工作中应用分子生物学',
])
def test_untitled_non_course_line_exits_instead_of_swallowing_text(work):
    text = f'主修课程：\n临床分子生物学检验\n{work}\n负责微生物培养'
    filtered = matching._course_free_corpus(textutil.normalize_text(text))
    assert '临床分子生物学检验' not in filtered
    assert textutil.normalize_text(work) in filtered
    assert '负责微生物培养' in filtered
    assert _state(_report(text)) == 'matched'


def test_newlines_and_original_same_line_boundary_are_preserved():
    text = '主修课程：临床分子生物学检验\r\n工作经历\r\n负责微生物检测'
    normalized = textutil.normalize_text(text)
    assert len(normalized.splitlines()) == 3
    filtered = matching._course_free_corpus(normalized)
    assert filtered == '工作经历\n负责微生物检测'


def test_course_knowledge_stays_available_for_capability_matching():
    text = '主修课程：\nPCR\n临床分子生物学检验'
    doc = requirements.requirements_from_jd_text('仪器操作能力')
    report = service.analyze_text(text, requirements_doc=doc)
    assert all(row['dimension'] == 'capability' for row in doc['requirements'])
    assert report['matching']['tag_hit_count'] >= 1
    assert 'PCR' in report['document'].get('text', text)


def test_course_exclusion_also_grounds_long_tag_evidence():
    text = '教育背景\n主修课程：\n' + '\n'.join(['临床检验基础'] * 8) + '\n分子生物学'
    assert _state(_report(text)) == 'not_found'


@pytest.mark.parametrize('kind', ['pdf', 'docx'])
def test_synthetic_extracted_wrapped_course_and_work(kind):
    from rn.resume_screening.ingest.document import load_bytes
    lines = ['教育背景', '主修课程：', '临床分子生物学检验', '工作经历', '负责样本处理和报告核对']
    if kind == 'pdf':
        import fitz
        doc = fitz.open()
        page = doc.new_page()
        for i, line in enumerate(lines):
            page.insert_text((72, 72 + i * 24), line, fontname='china-s')
        data = doc.tobytes()
        doc.close()
    else:
        from docx import Document
        doc = Document()
        for line in lines:
            doc.add_paragraph(line)
        stream = io.BytesIO()
        doc.save(stream)
        data = stream.getvalue()
    extracted = load_bytes(data, f'synthetic.{kind}')
    filtered = matching._course_free_corpus(textutil.normalize_text(extracted.text))
    assert '临床分子生物学检验' not in filtered
    assert '负责样本处理和报告核对' in filtered
    assert _state(_report(extracted.text)) == 'not_found'


def test_single_and_batch_reports_rank_real_work_above_courses(tmp_path):
    course = tmp_path / 'course.txt'
    work = tmp_path / 'work.txt'
    course.write_text('教育背景\n主修课程：\n临床分子生物学检验', encoding='utf-8')
    work.write_text('工作经历\n负责分子检测和报告核对', encoding='utf-8')
    doc = requirements.requirements_from_jd_text('分子或微生物检测经验')
    batch = service.analyze_resume_files([course, work], requirements_doc=doc)
    assert batch['ranking'][0]['filename'] == 'work.txt'
    by_name = {r['document']['filename']: r for r in batch['reports']}
    assert _state(by_name['course.txt']) == 'not_found'
    assert _state(by_name['work.txt']) == 'matched'
    single_files = service.write_report(by_name['course.txt'], out_dir=tmp_path / 'single')
    batch_files = service.write_batch_report(batch, out_dir=tmp_path / 'batch')
    assert single_files['markdown'] and batch_files['markdown']
