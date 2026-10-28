# Changelog

## Unreleased — PR #1 six-defect repair

- Disabled public static resume fallback; direct submission requires explicit DingTalk business acknowledgement.
- Age is a non-scoring compliance notice, never a recommendation or ranking gate; this supersedes historical age-gate descriptions below.
- Reports render contradicted and unknown states safely. Chinese line breaks survive normalization.
- Missing end months use same-month estimates; work entries expose `duration_estimated`.
- Exact-title JD templates supply unconfirmed wording references (`match_type=exact`), without creating requirements.
- Historical public resume links require separate operator revocation; this patch does not delete old files.


## Unreleased — Resume Screening Package (parsing, scoring, decision, JD generation)

- Semantics, scale, interview prompts, privacy and confirmation (P1/P2 hardening):
  - **Semantic coverage**: `analysis/textutil.py` folds common traditional/variant
    characters to simplified before matching (`醫學檢驗技術` → `医学检验技术`,
    `檢驗科` → `检验科`); cue lists gained `ISO15189`/`CNAS`/`CLIA`
    (规范操作), `LIS`/`HIS` (仪器操作) and `CAPA`/`PDCA` (问题分析).
  - **Batch cache / resumable runs**: `analyze --cache-dir <dir>` (`service.analyze_resume_files`)
    keys results by resume-bytes sha256 + requirements fingerprint and reuses them on
    re-runs; reports expose `cache: {enabled, hits, misses}`. Large batches no longer
    re-OCR unchanged files.
  - **Interview questions**: new `analysis/interview.py` turns `verification_items` and
    待核实 hard filters into per-item interview questions; reports render
    `## 面试问题（按待验证项生成）` right after 待验证清单. Questions are prompts only and
    never change a verdict.
  - **Privacy retention**: `scripts/purge-runs.py --older-than-days N [--yes]` cleans expired
    `workspace/runs` artifacts (dry-run by default), refuses paths outside `workspace/`,
    and only touches `workspace/test_resumes` with `--include-test-resumes`.
  - **JD confirmation trail**: `cli jd --confirm-by <name> --confirm-note <note>` writes
    `jd.confirmation.json` (confirmer/time/note/JD id) and appends
    `发布确认：已确认（…）` to `jd.quality.md`; without it the report says 待用人部门确认.
  - **CI hygiene**: the two data-foundation tests needing 14 original profile PDFs or the
    JD source Excel now `skip` when the source pack is absent (strict when it is present).

- Added an upload-to-match path inside `runtime/rn/resume_screening/`, independent
  of the v5 Candidate Decision chain: a resume file (PDF / DOCX / image / plain
  text) is turned into a `DocumentText`, parsed into structured fields, tagged on
  the three dimensions the policy already uses (capability / experience / trait),
  and compared against a job's three-dimension requirements.
  - `ingest/`: `document.py` (`load_document`, `load_bytes`, `DocumentText`,
    `OcrUnavailableError`) plus `ocr.py`. PDFs are extracted with PyMuPDF and
    scanned pages are rendered and OCR'd; DOCX paragraphs and tables are read;
    images go straight to OCR. The engine chain is Windows OCR (`Windows.Media.Ocr`,
    `powershell.exe` 5.1, `zh-Hans-CN`) → rapidocr → tesseract, and an unavailable
    engine raises instead of silently returning empty text. OCR output is repaired
    for CJK inter-character spaces, broken digests, `2021．07 一 2024．08` dates and
    confusions such as `1381234S678` → `13812345678`.
  - `analysis/fields.py`: deterministic extraction of 22 resume fields
    (`name`/`phone`/`email`/`education`/`work_experiences`/`certifications`/
    `skills`/`expected_*`/...), including multiple labels on one OCR line
    (`证书：…技能：…`, `性别：男年龄：27`, `.` read as `：`).
  - `analysis/requirements.py`: three-dimension requirements from the
    inspection-technician policy (profile overrides applied) or from JD text,
    with hard education/certification/location filters kept out of the three
    dimensions.
  - `analysis/tags.py` and `analysis/matching.py`: resume tags per dimension and
    a three-state match report (`matched` / `weak` / `not_found`), level-weighted
    coverage over the two scoring dimensions (capability 0.40 / experience 0.35,
    normalized between them; trait is reference-only, see below) and
    `interview_verify` items excluded from the coverage denominator and listed as
    interview checks — a resume that simply omits something is not scored as a
    mismatch.
  - Trait is not a scoring dimension: trait requirements and tags are still
    extracted and rendered (as interview focus), but the overall coverage is
    computed only from capability/experience (`analysis/cues.SCORING_DIMENSIONS`;
    trait lives in `REFERENCE_DIMENSIONS`). Trait must-have items never enter the
    gap list or the ranking, trait tags cannot change the percentage, and a JD
    made only of trait requirements reports「仅参考信息（无计分要求）」with no
    percentage instead of「无法评估」0%. Reports label the trait section
    「特质（不计分，仅参考）」and the score line「综合匹配度（辅助，仅能力/经验计分）」.
  - `service.py` / `cli.py` / `__main__.py`: `analyze_resume_file`,
    `analyze_resume_files`, `analyze_resume_bytes`, `write_report`,
    `write_batch_report`, and `python -m rn.resume_screening.cli analyze|requirements`
    with JSON/Markdown output.
- This match percentage is an advisory view only: it never writes back to the v5
  Candidate Decision result, and `tests/test_no_active_scoring_v5.py` keeps the
  active files free of numeric scoring.
- Added `tests/test_resume_screening_{ingest,fields,analysis,service,e2e}.py`
  (42 + 24 + 13 + 6 + 4 tests). The e2e tests build real DOCX/PDF/PNG resumes and
  require the same conclusion across formats.
- JD generation moved into the same package (`runtime/rn/resume_screening/jd/`):
  - `generator.py` (`build_request_jd` / `render_jd_summary`) merges request
    fields, the standard profile, and a related template into a structured JD
    (`schema_version 4.0`); hard requirements keep `HARD_GATE` / `VERIFY_ONLY`
    gate types and the template is marked `usage="wording_reference_only"`, so
    template wording can never leak in as a request-specific requirement.
  - `intake.py` (`HiringIntentAnalyzer`) is the LLM-only front end: natural
    language hiring request → `fields` + `profile_match`, with no keyword or
    regular-expression fallback.
  - `templates.py` (`JDRepository`) reads the verified 45-entry JD template
    library, keeping exact (`search_exact`) and related (`search_related`)
    matches separate and rejecting unverified entries.
  - `cli.py`: new `jd` subcommand (`--fields-file`, `--request-id`,
    `--profile-id` / `--profile-file`, `--related-jd-title`, `--library-file`,
    `--out`, `--json` / `--markdown` / `--no-write`, `--overwrite`) writing
    `jd.json` + `jd.md` under `workspace/runs/resume-screening-jd/`.
  - `rn/repositories` keeps a lazy `JDRepository` re-export, while
    `hiring_workflow_v4/v5`, `host_tool`, `data_doctor`, and the debug scripts
    now import from the new location. JD persistence (`hiring_requests`) and
    chat orchestration stay outside the package.
  - Added `tests/test_resume_screening_jd.py` (14 tests: generator, profile
    inheritance, related-template wording, template-library verification and
    health, and the `jd` CLI).
- Review hardening for the upload path (covered by
  `tests/test_resume_screening_review_fixes.py`, 28 tests):
  - JD text: sensitive attributes (gender/age/marital/parenthood) are split out
    line-by-line or fragment-by-fragment and never become requirements; a JD made
    only of hard filters is now reported as「仅硬性条件」with no coverage
    percentage instead of「无法评估」0%, and `strict` mode no longer rejects it;
    certificates mentioned in negation contexts (`尚未取得…`) are「待核实」, a
    certificate family mismatch (教师资格证 vs 检验士) is no longer satisfied by
    substring, and major matching only looks at the education block.
  - Fields: year-only ranges (`2019 - 2022`) count as 36 months / 3 years instead
    of 4, reversed ranges are dropped instead of producing negative months, and
    skills on a line after a certificate line are no longer swallowed.
  - Ingest: pages mixing a thin text layer with a scan now fall back to OCR,
    DOCX text boxes are extracted, UTF-16LE without BOM is decoded, and a PNG
    renamed to `.txt` is treated as an image rather than decoded as text.
  - CLI/service: GBK-encoded JD files load, exit codes are 0/2/3
    (input error vs analysis/write error), `--text` cannot be combined with
    files, empty requirements require `--allow-empty-requirements`, a batch where
    every file fails exits 3 without creating the output directory, and
    `write_report`/`write_batch_report` sanitize the stem and refuse to overwrite
    unless `--overwrite` is passed.
- Layout-robust extraction for platform-exported PDFs (covered by
  `tests/test_resume_screening_pdf_layout.py`, 12 tests):
  - Ingest: PDF pages are read through `page.get_text("blocks")` and re-sorted by
    visual position (y then x, or left-column → middle → right-column when a
    two-column gutter is detected) instead of PDF content-stream order, so section
    headers and their entries stay adjacent; platform watermark lines are dropped
    (long letter/digit/`~` tokens, `~`-terminated digests) before OCR/field parsing.
    The per-page text-layer threshold and OCR fallback are unchanged.
  - Fields: education sections support date-first layouts (`date / school / major`
    blocks) as well as entry-first; label fragments split across blocks (`姓` +
    `名：`) are merged and a value glued to the next label (`周英手机：`) is split
    back; certificate sections drop awards/volunteer lines, accept `技能证书` as a
    cert header, and clean bullet numbering plus unbalanced parentheses;
    OCR-split surname `弓长` is repaired to `张`; majors lose a trailing `专业`
    and empty/level parentheses.
- Publication-shaped JD output (`tests/test_resume_screening_jd.py`):
  - `jd/generator.py`: `render_jd_document(jd)` renders a posting — `# title`, meta
    (department / location / headcount / salary / work mode / onboard date),
    `## 岗位职责`, `## 岗位定位`, `## 任职要求` grouped into 学历 / 专业 / 经验 / 证书 /
    技能与能力 / 职业素养 / 其他硬性条件 by `requirement_groups`
    (clause splitting plus label-then-pattern classification), and `## 加分项`. `cli jd`
    writes this document to `jd.md` instead of the chat summary.
  - `build_request_jd` keeps `responsibilities` for user-supplied duties only; the
    standard-profile narrative moves to `profile_context` and is rendered under
    `## 岗位定位`, never as duties. A related JD template contributes duties and
    requirement wording through `related_template.reference`, rendered as an explicit
    wording reference ("发布前需用人部门确认"); with neither source the document asks
    for 3–5 duties instead of inventing them.
  - `cli jd` now auto-matches a template by position title (exact, then related) when
    `--related-jd-title` is absent, prints the document by default, and keeps the old
    chat summary behind `--summary` (`--json` still dumps the structured JD).
    `templates.JDRepository.search_related` gained a same-prefix bonus so「检验技术员」
    ranks「检验技师」above「实验室技术员」.
- External-posting JD standard, distilled from a mature recruiting tool's JD-generation
  reasoning and enforced by a checker (`docs/JD_WRITING_STANDARD.md`,
  `tests/test_resume_screening_jd_quality.py`, 20 tests):
  - `jd/quality.py`: `validate_jd(jd, document=..., external=...)` checks the three-section
    structure, an industry-generic title with no company terms, 5–7 duties of 15–30 chars
    starting with a strong verb, 5–7 KSAO-grouped requirements (学历专业 / 知识技能 /
    证书经验 / 职业素养), 400–600 chars for the external posting, and compliance against
    company terms (`JD_FORBIDDEN_COMPANY_TERMS`) and sensitive attributes (性别/婚育/年龄/
    籍贯/民族/户籍/身份证/政治面貌/宗教). `render_quality_report` prints a table plus
    ERROR/WARN lists; internal-mode compliance findings downgrade to WARN.
  - `jd/generator.py`: `render_jd_document(jd, external=True)` drops department/urgency/
    onboard date/`## 岗位定位` and company markers, unwraps internal JD ids from template
    references, filters sensitive values out of「其他硬性条件」, and adds a 素养 category;
    `sanitize_title` / `public_location` strip company and campus markers.
  - `jd/draft.py`: optional LLM drafting (`draft_request_jd`) whose prompt embeds the
    standard and whose output is merged back through `build_request_jd`;
    `JdDraftUnavailable` makes the CLI fall back to deterministic generation.
  - `jd/templates.py`: `is_related_title` (longest common substring ≥ 3) gates automatic
    template references, so a generic「医学检验技术员」no longer borrows a template that
    only shares「医学」.
  - `cli jd` gained `--external` (public posting), `--draft` (LLM draft with fallback) and
    `--check` (writes `jd.quality.md`, exits 4/`EXIT_QUALITY_ERROR` on any ERROR).
  - README, `docs/JD_WRITING_STANDARD.md` and the resume-parsing skill document the
    standard, thresholds, and the「发布前必须 --check」rule.
  - Delivery contract: the agent reply must contain the `jd.md` body itself — at least
    `## 岗位职责` and `## 任职要求`, verbatim — before any hard-condition/tag/screening
    content. A file path, a request-confirmation summary or a re-listing of the hiring
    fields is not a delivered JD; reused/unchanged JDs are re-read and pasted too
    (resume-parsing skill, 简历解析模式 persona, `docs/JD_WRITING_STANDARD.md`).
- Two-layer JD / matching-tag output (`cli jd --tags`, `tests/test_resume_screening_jd_quality.py`):
  - The JD is layer one: `jd/generator.py` renders `jd.md` from the standard profile plus the
    current request, and `## 任职要求` keeps its own group title「技能与能力」(`requirement_groups`).
    Only request-declared conditions are rendered there: `build_request_jd` keeps the merged
    profile abilities/experiences in `key_experience_skills` (tag source) and stores the
    user-supplied ones in `request_key_experience_skills`, which `requirement_groups` renders —
    profile capability names such as「快速学习能力」「检验科经验」no longer leak into the
    publishable JD as requirement bullets.
  - The matching tags are layer two, derived from that JD: `analysis/requirements.py`'s
    `render_requirements_markdown` renders `jd.tags.md` under the title
    「匹配标签（取自 JD：<岗位>，用于匹配候选人能力与经验）」with a「派生关系」line stating
    JD = profile + request and tags = derived from the JD. One table per dimension (编号 / 标签 /
    级别 / 判据方式 / 简历命中线索), a 计分标签 line (capability + experience = hit-rate
    denominator), and a hard-condition table (学历 / 证书 /
    工作地点 / 年龄 / 其他硬性条件) with「不满足 → 不推荐；未写明 → 待核实」actions.
    Trait requirements are not turned into tags at all: `requirements_from_jd` drops
    `dimension == "trait"` rows after derivation, so `jd.tags.md` and the chat view only
    carry capability/experience labels; JD 的「职业素养」stays in the publishable JD
    requirements as interview reference and never enters the hit rate.
  - `requirements_from_jd` routes KSAO-labelled hard requirements (经验 / 技能 / 职业素养 /
    能力 / 知识) into the scored tag body instead of dumping them into
    `hard_filters["other"]`, and lets explicitly declared JD hard conditions override
    body-derived ones (the 学历 filter no longer absorbs a「专业背景与项目经验」
    capability name); 专业 stays in `other` for manual major verification.
  - `cli jd --tags` prints the JD document followed by the tag view and writes `jd.tags.md`;
    `jd.md` stays publishable and never contains tag tables. Chat presentation order is fixed:
    JD 正文 → 匹配标签（取自 JD） → 候选人匹配结果.
  - README, the resume-parsing skill and the 简历解析模式 persona document the two layers and
    the internal-only rule.
- Three-section deliverable via `cli jd --presentation` (`jd/presentation.py`,
  `tests/test_resume_screening_jd_quality.py`):
  - `render_presentation(jd, *, external=False)` folds the two layers into one chat-ready
    document with exactly three `##` sections: 岗位职责, 任职要求 (加分项 folded in as a
    `- **加分项**：` bullet) and 匹配标签（取自 JD：<岗位>…）. The tag section lists **only the
    tags actually scored by `requirements_from_jd`** (one line each) — presentation and hit rate
    always share one tag set; profile `key_abilities` / `key_experiences` are no longer copied
    verbatim, and traits are never listed. Names stay short (`_condense_tag_name` trims leading
    verbs and caps at 12 characters; a new 分子或微生物检测经验 cue names the bonus-point case)
    so a duty sentence never becomes a tag name. Tables, 判据方式, 命中线索 and hard conditions
    stay in `jd.tags.md`. `jd.md` remains the two-section publishable file; `jd.json` /
    `jd.quality.md` are unchanged.
- Matching-accuracy fixes + human review sheet (`analysis/matching.py`,
  `analysis/requirements.py`, `scripts/make-review-sheet.py`, `scripts/eval-matching.py`):
  - Course context is no longer evidence of experience: experience tags are matched against a
    course-free corpus (`_COURSE_LINE_RE`: 主修课程/所学课程/课程：…), so a 分子生物学 **course**
    on a resume can no longer count as 分子或微生物检测经验.
  - Certificate aliases: 「临床医学检验技术（士/师）资格证」 is normalized to 检验士/检验师 before
    matching (`_CERT_ALIAS_RE`), so full certificate names are recognized instead of 待核实.
  - Generic words only: 仪器/设备/实验室/检验科/临床/样本/检测… no longer build a match on their
    own; an experience/capability match needs a strong keyword or ≥2 meaningful keywords in the
    same dimension (`_GENERIC_KEYWORDS`).
  - Major conditions: a KSAO「专业：…」line is routed into `hard_filters["education"]` and checked
    against the resume's education/major automatically instead of sitting in `other` as 待核实.
  - `scripts/make-review-sheet.py` writes a one-row-per-candidate review workbook
    (硬条件核验 + every scored tag + system verdict + 人工结论 dropdown 推荐/待核实/不推荐);
    `scripts/eval-matching.py --review-xlsx` reads it back and reports agreement with the rule
    verdict. On the 10 real resumes the fixes removed the false positive on 张雨's 检验科经验 and
    the two missed certificates; mean hit rate moved 55.0% → 46.25% (stricter, fewer inflations).
  - `cli jd --presentation` prints `jd.presentation.md` in the reply and writes it next to
    `jd.md` / `jd.tags.md`.
  - README, `docs/JD_WRITING_STANDARD.md`, the resume-parsing skill and the 简历解析模式 persona
    now require the reply body to be those three sections — a request summary or tag list is
    not a deliverable on its own.
- Tag-state semantics: 「未命中」 reserved for contrary evidence (`analysis/matching.py`,
  `scripts/eval-matching.py`, `scripts/make-review-sheet.py`, `scripts/make-tag-spec-sheet.py`):
  - `matched` → 命中（有证据）, `weak` → 待验证（有线索）, `not_found` → 待验证（简历未提及）,
    plus a new `contradicted` state → 未命中（有反证） for resumes that explicitly state they
    lack the experience/qualification. "简历没写" is never a rejection; it is an interview item.
  - Report gains `verification_items` and a 「待验证清单（面试/证件确认）」 section covering every
    unproven scored tag (weak + not_found), while `interview_pending` stays for
    `decision_mode=interview_verify` requirements.
  - audit.md / 人工校验表 / 标签口径表 show the 待验证 buckets; historical「未命中」 cells are
    normalized to 待验证（未提及） when computing agreement (unchanged: 10/10 verdicts,
    80/80 cells under the reviewer's corrected labels).
  - Batch ranking now leads with the hit count: the 排序总览 table (and `cli analyze` batch view)
    shows `命中/适用（百分比）`, then the 待验证项 names/count, then a separate 未命中（反证）count
    (`service.ranking[].pending_verification`, `tag_weak/missing/contradicted_count`).
  - `contradicted` is actually detected now: `_NEGATION_RE` + `_contradiction_evidence` flag
    resumes that write 「没有/未接触过/不具备」 in the same sentence as the requirement keyword
    (no more placeholder state), covered by `test_negative_statement_marks_contradicted`.
  - 快速学习能力 follows the same principle: a resume that merely writes 「快速学习能力/学习能力强」
    gets `?` weak-only keywords → 待验证（有线索）. 命中 requires **排名前 15% or better**
    (`_LEARNING_RANK_RE` + `_LEARNING_RANK_LIMIT = 15.0`, 专业排名前 40% → 待验证) or
    培训合格证书/奖学金; GPA/绩点 numbers alone never count (school difficulty isn't comparable).
    Review sheets now show each tag cell as 判定 + 依据（简历原句）+ 词（命中关键词）.
  - One verdict vocabulary: batch 排序总览 and the single-report score line now show only
    推荐 / 待核实 / 不推荐 (`matching.recommendation`); the tier `label`
    (高匹配/较高匹配/中等/偏低/低匹配) stays as an internal field and is no longer printed,
    so the recruiter no longer sees two competing verdict systems.
- Matching calibrated against a human review sheet of 10 real 检验技术员 resumes
  (`scripts/eval-matching.py`, `scripts/make-review-sheet.py`,
  `tests/test_resume_screening_tag_scoring.py`):
  - Fixed the corpus-folding bug that emptied experience matching: the course-free
    corpus is now built on the line-preserving text and passed into
    `_evaluate_requirement` (`course_free_corpus=`), while course-only evidence is
    detected on the raw line corpus (a compacted evidence snippet counts as a course
    quote when it still contains a course marker).
  - `analysis/matching.py`: new `recommendation` rule calibrated with the reviewer —
    disqualified → 「不推荐」, otherwise `tag_hit_rate >= 0.40` → 「推荐」, lower →
    「待核实」 (a thin tag list is not a rejection), no scored tags → empty.
  - `analysis/cues.py` keyword coverage widened from the review: 样本检测 (标本/样本
    处理系列), 分子或微生物 (微生物/PCR/分子检验/微生物上岗证/核酸提取), 快速学习
    (GPA/成绩排名/奖学金/培训合格证书), 团队合作 (社团/协助检验医师/配合完成),
    仪器操作 (检验仪器/仪器设备/上机操作), 专业理论 (临检/生化/免疫 组合),
    规范操作 (质控规则/质控结果); removed 临床检验/检验技术/适应能力 false positives.
  - `eval-matching.py --review-xlsx` compares the system against a filled
    `人工校验表.xlsx`: verdict agreement 10/10 = 100%, tag-cell agreement
    73/80 = 91.2% (up from ~55% before calibration); the remaining 7 differences are
    boundary cases (course context / weaker evidence) that change no verdict.
  - Final reviewer decisions (2026-10-26) encoded as rules instead of per-candidate
    exceptions: rotation lines (轮转/轮岗) count as experience contact (周清/王习之 口径),
    generic self-statements about training (参加学习培训/学习适应能力) carry the `?`
    weak-only marker so they can reach 待核实 but never upgrade to 命中
    (`Cue.weak_only_keywords`, `match_cues` `weak_only`, `tags._confidence` excludes
    them), and course/instrument names still never count as experience. With the
    corrected labels: verdict agreement 10/10, tag-cell agreement **80/80 = 100.0%**
    (`workspace\人工校验表-校验完毕-已决定.xlsx`).
- Tag-hit-rate scoring wired to generated JDs (`tests/test_resume_screening_tag_scoring.py`,
  25 tests: JD→tag splitting, age parsing, hit-rate scoring, age gate, CLI wiring):
  - `analysis/requirements.py`: new `requirements_from_jd(jd)` turns a generated JD into
    scoring tags — responsibilities + key experience/skills → capability/experience tags,
    bonus points →「优先」tags, and hard conditions split into
    `hard_filters` (`education` / `certification` / `location` / `age` / `other`).
    `parse_age_requirement` reads `25-35 岁` / `不超过35周岁` / `35 岁以下` / `25 岁以上`
    without mistaking `2019-2022 年` for an age range.
  - `analysis/matching.py`: the primary percentage is now the tag hit rate
    (`tag_hit_rate = tag_hit_count / tag_applied_count`, `score_basis="tag_hit_rate"`),
    counting only capability/experience tags; the previous level-weighted coverage is kept
    in `overall_coverage` for traceability. An explicitly requested age range is a hard
    gate: a resume age outside it sets `disqualified` / `recommendation="不推荐"`, an
    unknown resume age is「待核实」, and no age requirement means no age judgement.
    Other hard requirements are listed as「待核实」under `hard_filters["other"]` and never
    enter the denominator; batch ranking puts disqualified candidates last.
  - `service.py` / `cli.py`: `analyze` accepts `--fields-file` / `--jd-json` (mutually
    exclusive with `--jd-file` / `--job` and with each other) to score against
    generated-JD tags; every `analyze_*` helper accepts `requirements_doc=`; batch tables
    and reports show「标签命中率」, 命中/适用 counts, and the JD id.
  - README data flow, scoring semantics, CLI commands and exit codes updated.

## Unreleased — Resume Parsing/Screening Package Extraction

- Grouped every resume-parsing and resume-screening module into
  `runtime/rn/resume_screening/`, so the feature can be evolved as one unit:
  - `parsing/`: `resume_db.py`, `resume_jsonl.py`, `resume_mysql.py`,
    `resume_repository.py` (moved out of `rn/repositories/`).
  - `scoring/` (legacy v3/v4, not on the v5 active path): `scoring_engine_v3.py`,
    `scoring_v4.py`, `screening_pipeline_v3.py`, `screening_v4.py`.
  - `decision/` (v5 active path): `job_contract_v3.py`,
    `semantic_evidence_evaluator.py`, `evidence_grounding.py`,
    `decision_evidence_evaluator.py`, `criteria_adapter_v5.py`,
    `candidate_decision_v5.py`, `screening_v5.py`.
- All in-repo imports, scripts, tests, and the `scripts/verify.sh` v5 active-path
  guard now use the new paths; `ROOT = Path(__file__).resolve().parents[4]` in the
  moved modules keeps the policy file, run directory, and dev fixture resolving to
  the repository root.
- `rn.repositories` still re-exports `ResumeRepository` for existing callers, now
  lazily (module `__getattr__`) to avoid a circular import with
  `rn.repositories.models`.
- Added `runtime/rn/resume_screening/README.md` with the module map, data flow,
  and change guide.

## Unreleased — DingTalk Reply Routing & Resume Delivery Fixes

- Candidate Decision evidence is now evaluated twice per resume (`RECRUITER_DECISION_CONSISTENCY=strict`, the default) and every criterion whose two conclusions disagree falls back to `unknown`/待核实 instead of being decided by sampling noise. Measured on the 112-resume pool with the same policy: decisions that flipped between two identical runs dropped from 7/106 to 1/106, identical matched-tag counts rose from 80% to 88%, and the Top-3 overlap went from 1/3 to 2/3, at the cost of ~2.4x runtime per pool run (60s to 148s). `RECRUITER_DECISION_CONSISTENCY=off` restores single-pass evaluation.
- Each merged evaluation records a `consistency` block with the disagreement list, and a failed second pass logs `[decision] consistency_second_pass_failed code=...` while keeping the first result.
- Candidate criteria now follow the verified profiles instead of one social-hire-flavored set. The policy gained the 2026.09.20 profile items (`试剂耗材管理能力`, `专业理论知识`, `专业背景与项目经验`, `持续学习`), criterion ids are declared in the policy itself (C01-C12 keep their meaning, new items are C13-C16), and `profile_criterion_overrides` may mark an item `applicable: false`, so each profile is evaluated only against its own items and the tag denominator matches it: 社招 13 criteria / 16 tags, 应届 11 criteria / 14 tags. Previously a 应届 candidate was measured against 社招 abilities such as 试剂耗材管理能力, and both profiles reported the same denominator.
- Each screening run records the skipped items in `actual_criteria.json` (`profile_criterion_exclusions`), and `rn policy` reports `criteria_by_profile`. `tests/test_policy_profile_coverage.py` fails whenever a profile item has no matching policy criterion, so the next profile update cannot silently drift again.
- Added `scripts/update-profile-from-docx.py` to rebuild one profile entry from a 岗位画像 DOCX table.
- Stopped showing trait items (抗压 / 细心 / 责任心 / 积极主动 / 持续学习) as 推荐依据 to the hiring manager: trait conclusions still flow into 面试重点/待核实 but do not justify a recommendation. Recommended pool ordering and matched tag counts are unchanged; on the 112-person pool, 6 people lost the only line of 推荐依据 that came from a trait.

- Fixed the SELECTED takeover: a hiring manager's earlier selected request can no longer intercept every later message. Active requests keep priority, and resume/contact intents only apply when the message actually refers to the selected request. This restores field intake ("紧急") and stops unrelated replies from being read as contact consent.
- Fixed ordinal selection ("选第N个") so it applies to the request that displayed the batch, instead of an older SELECTED request; the selection message is now the first-selection wording.
- Stopped promising the enterprise cloud contact-center (Tencent CCC) call flow: selecting a candidate no longer asks about it, and an explicit contact request now states that the capability is not integrated yet and that no call or recording will be placed. See docs/reports/TENCENT_CCC_CONTACT_IMPLEMENTATION_HANDOFF_20260921.md.
- Resume delivery now fails with an actionable reason (`RESUME_SOURCE_ROOTS_NOT_CONFIGURED`) when `RECRUITER_RESUME_SOURCE_ROOTS` is unset, instead of reporting only "not ready".
- Added host diagnostics for intent-classifier failures and resume-delivery failures; the DingTalk bridge forwards child-process diagnostics to journalctl.
- Documented `RECRUITER_RESUME_SOURCE_ROOTS`, `RECRUITER_RESUME_MAX_BYTES`, `RECRUITER_RESUME_URL_BASE`, `RECRUITER_RESUME_URL_DIR`, `DINGTALK_AI_CARD_TEMPLATE_ID`, `DINGTALK_PROGRESS_CARD`, and `DINGTALK_PROGRESS_THROTTLE_MS`; deployment scripts create `workspace/resume-url` and exclude `*.backup-*` from the release sync.
- Moved stale `runtime/rn/*.backup-*` recovery copies out of the deploy path and refreshed the stale `/new` router expectation in `im/router-v34.test.mjs` (release verification gate).
- Fixed duplicate hiring requests: the intent classifier now receives the pending intake fields (`当前待补字段`), and the prompt boundary states that supplying those field values continues the current draft even when the message reads like a full hiring description. Previously each field answer created a new request.
- Stopped leaking backend errors to hiring managers: a screening backend failure (e.g. missing MySQL configuration) now keeps the request retryable at `JD_READY` with a safe message, while the real reason is logged as `[workflow] screening_failed request=… error=…`; the DingTalk bridge no longer appends `result.error` to the user reply.
- Fixed duplicate DingTalk Stream consumers: starting a second local bridge while a stale instance stayed connected made DingTalk route robot messages to a dead connection, so the hiring manager saw the platform error card (`INTERNAL_UNKNOWN`). `scripts/start-local.sh` now refuses to start when an instance is already running, and `scripts/stop-local.sh` / `stop-local.cmd` stop every instance.
- DingTalk inbound messages are no longer dropped silently: payload parse failures and unnormalizable messages (missing `senderStaffId`/`msgId`) are logged with a field-presence summary. `DINGTALK_STREAM_DEBUG=true` enables the SDK raw frame log for troubleshooting (off by default because frames contain the session webhook).
- Fixed garbled DingTalk replies: the bridge now spawns the Python host with `PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8`. On Windows Python encodes stdout with the console code page (GBK) while the bridge decodes it as UTF-8, which turned every reply into mojibake. `scripts/start-local.sh`, `deploy/recruiter-na.service` and the env examples set the same variables.
- Fixed empty bodies for markdown DingTalk messages: the adapter now reads `text.content`, `markdown.text` or `content`, logs an empty body instead of failing silently, and the field summary reports markdown presence. Previously such a message fell through to the Codex fallback and failed with `spawn codex ENOENT`.
- Candidate Decision now vetoes a required criterion that the resume explicitly disproves: `level=必须` + `decision_mode=resume_evidence` + `criterion_result=not_satisfied` + `evidence_status=confirmed` fails the candidate (`rule=resume_evidence_confirmed_not_satisfied`). `unknown` and `partial` evidence still only produce verification items. On the labelled regression set this moves a candidate the business rejected from `pass` to `fail` while leaving the two accepted candidates unchanged.
- Candidate Decision is now deterministic: the evidence request sets `temperature=0`. Previously the same resume could yield different criterion verdicts between runs, which decision rules then amplified into wrong rejections.
- Contract violations in the evidence document are repaired instead of aborting the candidate: a definitive result without a verbatim quote is downgraded to `unknown` (hard checks and red lines to `unknown`, criteria to `unknown`/`none`), `none` with evidence becomes `partial`, and out-of-order assessments are re-sorted. Repairs are recorded in `contract_repairs`. Previously one ungrounded quote raised `EvidenceEvaluationError` and the candidate silently disappeared as a technical failure.
- Added `scripts/eval-resumes.py`: a MySQL-free evaluation regression that turns resume PDFs into resume facts, runs the semantic evidence chain plus the deterministic decision, and compares the result with business labels.
- Criteria now follow the matched profile instead of the shared policy alone. The policy supports both 检验技术员（应届）and 检验技术员（社招）but described only the experienced bar (≥1年 inspection experience, level=必须), so a fresh-graduate requisition evaluated interns against it and vetoed every one of them. `inspection_technician_v1.json` gained `profile_criterion_overrides` (fresh graduates: experience is a preference, internships suffice and a candidate who needs training is not vetoed; experienced hires: ≥1年 stays required) and the adapter applies them per matched profile, recording `profile_criterion_overrides_applied` in the criteria contract. `scripts/eval-resumes.py` gained `--profile-id` so a regression can run under either bar.

## 0.1.0 — Public Source Release

- Published the Recruiter-Na v5.0.2 source, safe example configuration, deployment scripts, and verification suite under Apache-2.0.
- Excluded credentials, tenant configuration, runtime state, candidate resumes, internal position/JD libraries, request databases, historical internal materials, and the separately sourced `codexharness-im` adapter.
- Documented the public product boundary: owner-scoped hiring requests and approved-policy Candidate Decision only; no public HR-admin data browsing or numerical candidate scoring.

## 5.0.2 — Candidate Recall & Qwen Decision Routing Hotfix

- Fixed inspection-technician candidate recall for Chinese resumes that contain `医学检验` / `检验科` / `检验` but not the exact title `检验技术员`; recall aliases are now part of the internal Policy rather than ingestion-specific raw JSON.
- Candidate Decision now prefers the approved interactive chain `qwen3.6-flash → qwen3.7-max` when no explicit decision model is configured.
- Added v5.0.2 env migration that replaces the v5.0.1 auto-generated DeepSeek decision primary with Qwen only when those Qwen models are already present in the operator-approved legacy fallback list; explicit custom decision models remain untouched.
- Kept the 15s/model, one-attempt, two-model interactive budget; no default one-minute sleep is added for generic read timeouts.

## 5.0.1 — Candidate Decision Latency & Grounding Hotfix

- Fixed production grounding false failures: an exact resume quote with a wrong model-authored `source_path` is now canonicalized to the authoritative path.
- Added fail-safe grounding repair: ungrounded definitive evidence is downgraded to `UNKNOWN`/`none`; it can never support rejection or preference, and a single bad quote no longer crashes the whole candidate evaluation.
- Added decision-specific interactive LLM controls: 15s timeout, 1 request attempt per model, and at most 2 models by default.
- When no dedicated decision fallback chain is configured, existing `flash`/`fast` fallback models are preferred for the Candidate Decision path.
- Added per-candidate `timing.json` plus aggregate screening performance metadata and a regression test proving candidate workers execute concurrently.
- Added `upgrade-v5.0.1.sh`; it preserves global LLM configuration and only appends missing decision-specific latency settings.

## 5.0.0 — Candidate Decision Migration

- Replaced the active RQ/JM/CS candidate-scoring path with evidence-grounded Candidate Decision.
- Accepted recruiter workflow v2.3 as the business Decision Policy baseline for the inspection-technician MVP.
- Added separate `criterion_result` and `evidence_status` contracts; UNKNOWN never becomes an automatic rejection.
- Added deterministic hard-check / decision-mode rules and grounded veto requirements.
- Added `criteria_adapter_v5.py`, `decision_evidence_evaluator.py`, `candidate_decision_v5.py`, `screening_v5.py`, and `hiring_workflow_v5.py`.
- Preserved v4 hiring-manager intake, request-specific JD, owner isolation, Top 3 / next batch, selection, pause/resume and HR-sourcing continuation.
- Kept the existing `ranking_json` database column for migration safety; v5 stores recommendation order, not a numeric candidate score.
- Added explicit role-policy scope gate: unsupported roles never fall back to the legacy scorer.
- Moved scoring v3/v4 modules to legacy/non-active status without deleting them from the source bundle.

## 4.0.0 — Hiring Manager Recruitment Workflow

- Repositioned Recruiter-Na as a DingTalk digital recruiter for hiring managers, not an HR/admin database bot.
- Public capabilities reduced to: start/continue own hiring requests; view own hiring progress.
- Added owner-scoped SQLite Recruitment Request store keyed by normalized DingTalk senderId.
- Added semantic-only standard-profile matching. No keyword fallback may decide profile membership.
- Matched profiles reduce intake fields; unmatched roles collect complete hiring information.
- Added request-specific JD generation; related JD templates are wording references only.
- Added v4 dual semantic scoring: ProfileMatch 40% + JDMatch 60%, or JDMatch 100% without profile; outer RQ 30% + JM 70% retained.
- Fixed old top-3 logic: v4 scores the retrieved candidate pool first, persists one ranking snapshot, and serves non-repeating batches of three.
- Added candidate selection, pause/resume, WAITING_HR_SOURCING, and new-resume continuation semantics.
- Removed profile/JD/resume full-library browsing from the hiring-manager product path.
- Archived v3.x public/admin-oriented Skills outside `.agents/skills`; only v4 hiring-request and my-hiring-progress remain discoverable.
