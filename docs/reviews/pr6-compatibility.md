# PR #6 修复与兼容性验证

日期：2026-10-29。验证使用本地合成回归，没有访问生产 MySQL、发送业务消息或部署。
完成本地修复后，用户另行授权更新原 PR 并在满足兼容性与优化目标时合并；远端合并结果以 GitHub PR 状态为准。

## 整合范围

- PR #6 原始 head：`de0a60045964a714d9eaca85fbfee2b9217cc3bf`。
- 原始共同祖先：`46797ce9e03c0f2ba4b869c4c1e780665b6acebe`，尚未包含 PR #5。
- 本次基础：最新 main `4943f92b0594ce53d1948ee382262106f3d02cfd`，已包含 PR #5 和 PR #7。
- GitHub PR 创建时间 08:14:43 UTC，PR #5 合并时间 08:14:25 UTC；虽然 PR 创建稍晚，代码共同祖先仍早于 #5。
- 以最新 main 整合原始 #6，保留双方提交历史。唯一文本冲突位于 resume_screening README；保留阶段分组和面试提问说明，并补入标准标签说明。

## 修复

仅位于 hard_requirements 的标准能力/经验标签，在跳过硬门槛处理前加入标签正文，沿用原有归类和去重。
新增四组解析回归覆盖字符串/字典硬要求及跨字段去重，一组集成回归覆盖简历与面试阶段分组。
修复前新增用例结果为 3 failed / 2 passed，失败原因是标准标签被丢弃；修复后全部通过。

PR #5 的阶段分组仍有效：面试确认项同时排除于简历分子、分母，缺少证据的简历核验项仍保留分母。
PR #7 的工单焦点、本人删除和权限回归纳入专项。v5 决策、排序、policy、工作流与工单实现文件相对最新 main 无修改。
更新日志只记录本次可验证行为，移除原 PR 基于旧阶段口径的批次分布，未将其当成本次验证。

## 优化目标与合并判断

结论：修复后的实现能完成提出者的标准标签识别优化，对 JD 要求的完整性有益，适合合入当前主分支。

- 原有启发式会把标准标签「沟通表达能力」当作特质丢弃，把「专业背景与项目经验」当作学历条件；精确词表名称优先能消除这两类误判，不增加 JD 未提出的标签。
- 复核发现的硬要求独有标签丢失也已修复。字符串与结构化硬要求均保留标准标签，跨字段重复不会扩大标签数，真实学历条件仍保留。
- 提出者的目标按现行阶段合同实现：JD 总标签由本次 JD 决定；简历卡片只统计其中本阶段可核验项。恢复「沟通表达能力」不意味着它必须进入简历分母，面试确认项仍按 PR #5 排除。
- 当前 main 已有的 PR #5/#7 实现全部保留，未覆盖决策、排序、policy、工作流或工单文件；新增回归和既有专项通过，全量对照未增加失败。
- 解析结果和相应展示标签的变化是本次预期优化。兼容判断以以上范围和证据为准，不将有限测试表述为任意输入下的绝对保证，也不将合并视为生产或业务验收。

如 GitHub 要求人工审批，可使用：

> 同意合并修复后的 PR #6。标准标签按词表精确归类，补齐仅位于硬性要求中的标签并保持去重；已基于当前主分支保留 PR #5 的简历/面试阶段口径和 PR #7 的本人需求管理。专项 114 项、IM 12 项通过，全量对照为主分支 528 通过、修复后 534 通过，相同 8 项环境/资料失败、无新增失败。该变更改善 JD 标签完整性，不改 v5 资格、决策、推荐顺序或 Policy；同意源码合并，部署与业务验收另行处理。

## 验证

命令前缀 `PYTHONPATH=runtime`。专项使用现有 Python 环境；最终全量对照使用 `/tmp/pr6-verification-venv/bin/python`，该临时环境继承系统包，并补充 pypdf、PyMuPDF；未改仓库依赖或复制私有材料。

| 检查 | 结果 |
| --- | --- |
| JD 解析、可读标签、工单焦点/删除、工作流、筛选、决策六个测试文件 | 114 passed |
| `node --test im/*.test.mjs` | 12 passed |
| main 源码快照：`python -m pytest tests -q` | 528 passed / 8 failed / 10 skipped |
| 修复后：同环境同命令 | 534 passed / 相同 8 failed / 10 skipped |
| `python -m compileall -q runtime scripts` | 通过 |
| `python scripts/secret_scan.py` | SECRET_SCAN_CLEAN |
| 差异空白检查 | 通过 |

完整失败标识集合由脚本比较一致；新增六项通过包括原 #6 的一项和本次五项。全量未全绿，不代表部署或业务验收。
两侧都有 5 条 PyMuPDF SWIG 类型弃用警告，没有因修复而变化。

最终两侧共有失败（缺少源码未提供的本地资料、由此引起的模板/画像查找失败，以及无可用 OCR）：

- `tests/test_data_foundation_v34.py::test_resume_source_pack_is_manifested_but_not_runtime_authority`
- `tests/test_repositories_v34.py::test_jd_repository_exact_and_related_are_separate`
- `tests/test_repositories_v34.py::test_profile_repository_reads_only_verified_14_profile_library`
- `tests/test_resume_screening_jd.py::test_cli_inherits_profile_by_id`
- `tests/test_resume_screening_jd.py::test_cli_uses_related_template_for_wording`
- `tests/test_resume_screening_jd.py::test_default_jd_library_is_verified_and_searchable`
- `tests/test_resume_screening_jd.py::test_related_jd_prefers_same_domain_prefix`
- `tests/test_resume_screening_review_fixes.py::test_png_with_txt_suffix_is_not_decoded_as_text`

## 初始环境探测

在补齐临时测试依赖前，系统 Python 运行出现 19 项失败；原目录现有虚拟环境全量对照两侧各出现 28 项失败（主分支 508 passed，修复后 514 passed）。依赖缺失涉及 fitz、pypdf、PIL 等，故使用上面的临时环境重新做完整对照。以下列出这些初始运行中最终环境之外的失败，避免隐藏测试记录；未通过修改业务实现或测试来消除它们：

- `tests/test_course_continuation.py::test_synthetic_extracted_wrapped_course_and_work[pdf]`
- `tests/test_pr1_six_regressions.py::test_synthetic_document_line_boundaries_survive_ingestion[pdf]`
- `tests/test_resume_screening_e2e.py::test_pdf_resume_end_to_end`
- `tests/test_resume_screening_ingest.py::test_corrupt_pdf_raises_value_error`
- `tests/test_resume_screening_ingest.py::test_encrypted_pdf_raises_value_error`
- `tests/test_resume_screening_ingest.py::test_explicit_media_type_alias`
- `tests/test_resume_screening_ingest.py::test_image_extension_content_mismatch_prefers_magic`
- `tests/test_resume_screening_ingest.py::test_image_ocr_empty_result_is_empty_text_with_warning`
- `tests/test_resume_screening_ingest.py::test_image_ocr_unavailable_raises`
- `tests/test_resume_screening_ingest.py::test_load_docx_with_embedded_image_falls_back_to_ocr`
- `tests/test_resume_screening_ingest.py::test_load_image_uses_injected_engine_and_normalizes`
- `tests/test_resume_screening_ingest.py::test_load_mixed_pdf_records_both_engines`
- `tests/test_resume_screening_ingest.py::test_load_multiframe_tiff_ocr_per_frame`
- `tests/test_resume_screening_ingest.py::test_load_pdf_bytes_without_path`
- `tests/test_resume_screening_ingest.py::test_load_pdf_text_layer`
- `tests/test_resume_screening_ingest.py::test_load_scanned_pdf_uses_ocr`
- `tests/test_resume_screening_ingest.py::test_ocr_chain_all_broken_raises`
- `tests/test_resume_screening_ingest.py::test_ocr_chain_falls_back_to_next_engine`
- `tests/test_resume_screening_ingest.py::test_scanned_pdf_without_engine_raises`
- `tests/test_resume_screening_review_fixes.py::test_mixed_pdf_page_falls_back_to_ocr`
