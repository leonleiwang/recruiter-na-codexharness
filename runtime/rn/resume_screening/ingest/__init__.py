"""简历文件读取层（ingest）：把 PDF / DOCX / 图片 / 纯文本统一成 ``DocumentText``。

对外入口（由 ``document.py`` 提供，``__init__`` 不 eager import 以保持包导入轻量）::

    from rn.resume_screening.ingest.document import load_document, load_bytes, DocumentText

职责边界：
- 只负责「文件 → 文本 + 引擎信息」，不做字段抽取、不做任何判定；
- 所有识别引擎可插拔（Windows OCR / rapidocr / tesseract / 测试注入），
  引擎不可用时抛出 ``OcrUnavailableError``，绝不返回静默空字符串；
- 扫描件（PDF 无文本层、图片）必须走 OCR，并在 ``DocumentText.engines``
  里记录实际使用的引擎，便于回溯证据来源。
"""
