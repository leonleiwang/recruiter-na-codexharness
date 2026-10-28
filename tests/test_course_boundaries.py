"""Goal 5B: course context wins over fragments; actual work wins over courses."""
import pytest

from rn.resume_screening import service
from rn.resume_screening.analysis import matching, requirements, textutil


def _analyze(text):
    return service.analyze_text(text, requirements_doc=requirements.requirements_from_jd_text('分子或微生物检测经验'))


def _state(report):
    return report['matching']['dimensions']['experience']['items'][0]['state']


@pytest.mark.parametrize('courses', [
    '教育背景\n主修课程：\n临床分子\n生物学检验',
    '主修课程：\n解剖学（必修）\n临床分子生物学检验',
    '主修课程：\n• 解剖学（选修，2学分）\n• 临床分子\n  生物学检验',
    '主修课程：\n临床生化\n临床分子生物学检验',
    '主修课程：\n解剖学（学分：2）\n临床分子\n生物学检验',
    '主修课程：\n解剖学（必修，学分：2）\n临床分子\n生物学检验',
])
def test_course_context_does_not_require_complete_subject_suffix(courses):
    normalized = textutil.normalize_text(courses)
    assert '\n' in normalized
    filtered = matching._course_free_corpus(normalized)
    assert '分子' not in filtered
    report = _analyze(courses)
    assert _state(report) == 'not_found'
    assert report['matching']['tag_hit_rate'] == 0
    assert report['matching']['recommendation'] == '待核实'
    reference = _analyze('教育背景\n主修课程：解剖学')
    assert matching.match_rank_key(report['matching']) == matching.match_rank_key(reference['matching'])


@pytest.mark.parametrize('rotation', ['轮训', '转科', '轮转', '轮岗'])
def test_all_existing_rotation_markers_override_course_continuation(rotation):
    work = f'检验科{rotation}：微生物培养'
    text = f'主修课程：\n临床检验基础\n{work}'
    filtered = matching._course_free_corpus(textutil.normalize_text(text))
    assert work in filtered
    assert '临床检验基础' not in filtered
    report = _analyze(text)
    assert _state(report) == 'matched'
    assert report['matching']['tag_hit_rate'] == 1
    assert report['matching']['recommendation'] == '推荐'


@pytest.mark.parametrize('boundary', ['工作经历', '实习经历', '项目经历', '负责样本检测', '检验科轮训：标本处理'])
def test_real_work_exits_context_after_fragment_and_annotation(boundary):
    text = f'主修课程：\n解剖学（必修）\n临床分子\n生物学检验\n{boundary}\n微生物培养'
    filtered = matching._course_free_corpus(textutil.normalize_text(text))
    assert '临床分子' not in filtered
    assert '生物学检验' not in filtered
    assert boundary in filtered
    assert '微生物培养' in filtered
    assert _state(_analyze(text)) == 'matched'


def test_ambiguous_nominal_course_text_is_not_automatically_work_evidence():
    text = '主修课程：\n分子\n这是没有标题的个人叙述。\n负责微生物培养'
    filtered = matching._course_free_corpus(textutil.normalize_text(text))
    assert '分子' not in filtered
    assert '这是没有标题的个人叙述。' in filtered
    assert '负责微生物培养' in filtered
    assert _state(_analyze(text)) == 'matched'


def test_annotation_and_fragments_do_not_bypass_tag_grounding_or_batch_rank(tmp_path):
    course = tmp_path / 'course.txt'
    work = tmp_path / 'work.txt'
    course.write_text('主修课程：\n解剖学（必修）\n临床分子\n生物学检验', encoding='utf-8')
    work.write_text('检验科转科：微生物培养', encoding='utf-8')
    batch = service.analyze_resume_files([course, work], requirements_doc=requirements.requirements_from_jd_text('分子或微生物检测经验'))
    reports = {r['document']['filename']: r for r in batch['reports']}
    assert reports['course.txt']['tags']['tags']['experience']  # real tag extractor did find keywords
    assert _state(reports['course.txt']) == 'not_found'
    assert _state(reports['work.txt']) == 'matched'
    assert batch['ranking'][0]['filename'] == 'work.txt'
    assert service.write_report(reports['course.txt'], out_dir=tmp_path / 'single')['markdown']
    assert service.write_batch_report(batch, out_dir=tmp_path / 'batch')['markdown']
