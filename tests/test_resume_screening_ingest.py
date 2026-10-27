"""resume_screening.ingest 层单测：DocumentText / load_document / load_bytes / OCR 链。

说明：
- 本机 pytest 的 ``tmp_path`` 有 Windows ACL 问题（PermissionError WinError 5），
  因此这里的临时目录全部用 ``tempfile.gettempdir()`` 下手建（mode=0o777）并清理；
- 除一个「真实 Windows OCR」用例外，其余全部注入假引擎（``recognize(png_bytes)``），
  不依赖网络、不依赖本机是否装了 tesseract/rapidocr；
- PDF / DOCX 都在测试里现场用 PyMuPDF / python-docx 生成，不读取私有数据。
"""

from __future__ import annotations

import dataclasses
import io
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _path in (str(ROOT / "runtime"), str(ROOT / "workspace")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from rn.resume_screening.ingest.document import (  # noqa: E402
    DocumentText,
    load_bytes,
    load_document,
)
from rn.resume_screening.ingest.ocr import (  # noqa: E402
    OcrUnavailableError,
    WindowsOcrEngine,
    normalize_ocr_text,
    resolve_engines,
    run_ocr_chain,
)

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------------------
# 临时目录 / 假引擎 / 素材生成
# ---------------------------------------------------------------------------


def _temp_dir() -> Path:
    base = tempfile.gettempdir()
    path = Path(os.path.join(base, "rn-ingest-" + uuid.uuid4().hex))
    path.mkdir(mode=0o777, parents=True, exist_ok=True)
    return path


@pytest.fixture()
def workdir():
    """替代 tmp_path 的可写目录（规避本机 tmp_path ACL 问题）。"""
    path = _temp_dir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class FakeOcrEngine:
    """固定返回预置文本的假引擎，并记录每次调用。"""

    def __init__(
        self,
        text: str = "姓 名 ： 张 晓 明\n电 话 ： 13812345678\n邮 箱 ： zhangxm@example.com",
        name: str = "fake-ocr",
    ) -> None:
        self.text = text
        self.name = name
        self.calls: list[bytes] = []

    def recognize(self, png_bytes: bytes) -> str:
        assert isinstance(png_bytes, bytes)
        assert png_bytes.startswith(_PNG_MAGIC), "OCR 引擎协议要求传 PNG 字节"
        self.calls.append(png_bytes)
        return self.text


class BrokenOcrEngine:
    """直接抛 OcrUnavailableError 的引擎，用于测链式降级。"""

    name = "broken-ocr"

    def __init__(self) -> None:
        self.calls = 0

    def recognize(self, png_bytes: bytes) -> str:
        self.calls += 1
        raise OcrUnavailableError("引擎在本机不可用")


class EmptyOcrEngine:
    """能跑但识别不出任何文字的引擎。"""

    name = "empty-ocr"

    def __init__(self) -> None:
        self.calls = 0

    def recognize(self, png_bytes: bytes) -> str:
        self.calls += 1
        return "  \r\n\t "


def _cjk_font(size: int = 26):
    from PIL import ImageFont

    fonts_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for name in ("msyh.ttc", "simsun.ttc", "simhei.ttf", "msyhbd.ttc", "simkai.ttf"):
        path = fonts_dir / name
        if not path.is_file():
            continue
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            continue
    return ImageFont.load_default()


def make_png(lines: str | list[str] | None = None, size=(900, 620), font_size: int = 26) -> bytes:
    """生成一张白底黑字 PNG（用于真实 OCR 与假引擎输入）。"""
    from PIL import Image, ImageDraw

    if lines is None:
        lines = [
            "个人简历",
            "姓名：张晓明",
            "电话：13812345678",
            "邮箱：zhangxm@example.com",
        ]
    if isinstance(lines, str):
        lines = lines.split("\n")
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    font = _cjk_font(font_size)
    y = 24
    for line in lines:
        draw.text((28, y), line, fill="black", font=font)
        y += font_size + 18
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def make_pdf(*, text: str | None = None, scanned: bool = False) -> bytes:
    """用 PyMuPDF 现场生成 PDF：文字页（china-s 中文字体）或纯图片扫描页。"""
    import fitz

    document = fitz.open()
    try:
        page = document.new_page()
        if scanned:
            page.insert_image(fitz.Rect(40, 40, 560, 380), stream=make_png())
        else:
            page.insert_text(
                (72, 110),
                text
                or "姓名：张晓明 电话：13812345678 邮箱：zhangxm@example.com 学历：本科",
                fontname="china-s",
                fontsize=14,
            )
        return document.tobytes()
    finally:
        document.close()


def make_mixed_pdf() -> bytes:
    """第 1 页有文本层、第 2 页是纯图片（模拟扫描件）。"""
    import fitz

    document = fitz.open()
    try:
        text_page = document.new_page()
        text_page.insert_text(
            (72, 110),
            "姓名：张晓明 电话：13812345678 邮箱：zhangxm@example.com 学历：本科",
            fontname="china-s",
            fontsize=14,
        )
        scan_page = document.new_page()
        scan_page.insert_image(fitz.Rect(40, 40, 560, 380), stream=make_png())
        return document.tobytes()
    finally:
        document.close()


def make_docx(paragraphs: list[str] | None = None, table_rows: list[list[str]] | None = None) -> bytes:
    """用 python-docx 现场生成 DOCX（可选段落 + 表格）。"""
    from docx import Document as DocxDocument

    document = DocxDocument()
    for line in paragraphs or []:
        document.add_paragraph(line)
    if table_rows:
        table = document.add_table(rows=len(table_rows), cols=len(table_rows[0]))
        for row_index, row in enumerate(table_rows):
            for column_index, value in enumerate(row):
                table.cell(row_index, column_index).text = value
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def make_image_docx() -> bytes:
    """只含一张内嵌图片、没有任何文字的 DOCX（模拟扫描件转 Word）。"""
    from docx import Document as DocxDocument
    from docx.shared import Inches

    document = DocxDocument()
    document.add_picture(io.BytesIO(make_png()), width=Inches(4))
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def make_multiframe_tiff() -> bytes:
    """两帧 TIFF（模拟多页扫描）。"""
    from PIL import Image

    frames = [
        Image.new("RGB", (400, 160), "white"),
        Image.new("RGB", (400, 160), "white"),
    ]
    buffer = io.BytesIO()
    frames[0].save(
        buffer,
        format="TIFF",
        save_all=True,
        append_images=frames[1:],
    )
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# 契约：DocumentText 字段
# ---------------------------------------------------------------------------


def test_document_text_field_order_and_to_dict():
    names = [field.name for field in dataclasses.fields(DocumentText)]
    assert names == [
        "source_path",
        "filename",
        "media_type",
        "text",
        "page_count",
        "engines",
        "warnings",
        "meta",
    ]
    document = DocumentText(
        source_path="",
        filename="a.txt",
        media_type="text",
        text="x",
        page_count=1,
        engines=["plain-text"],
        warnings=[],
        meta={"k": "v"},
    )
    payload = document.to_dict()
    assert payload["engines"] == ["plain-text"]
    assert payload["meta"] == {"k": "v"}
    assert isinstance(payload["engines"], list) and isinstance(payload["meta"], dict)


# ---------------------------------------------------------------------------
# 纯文本
# ---------------------------------------------------------------------------


def test_load_text_utf8_with_bom(workdir):
    path = workdir / "简历.txt"
    path.write_bytes("\ufeff姓名：李四\r\n电话：13900000000\r\n".encode("utf-8"))
    document = load_document(path)
    assert document.media_type == "text"
    assert document.filename == "简历.txt"
    assert document.text == "姓名：李四\n电话：13900000000\n"
    assert document.page_count == 1
    assert document.engines == ["plain-text"]
    assert document.meta["encoding"] == "utf-8-sig"
    assert document.meta["bytes"] == path.stat().st_size
    assert len(document.meta["sha256"]) == 64
    assert document.source_path == str(path.resolve())


def test_load_text_gbk_fallback(workdir):
    path = workdir / "resume_gbk.txt"
    path.write_bytes("姓名：王五\n学历：大专\n".encode("gb18030"))
    document = load_document(path)
    assert "王五" in document.text
    assert "大专" in document.text
    assert document.meta["encoding"] in {"gb18030", "gbk"}


def test_load_text_unknown_extension_uses_content(workdir):
    path = workdir / "resume.data"
    path.write_bytes("姓名：赵六\n".encode("utf-8"))
    document = load_document(path)
    assert document.media_type == "text"
    assert "赵六" in document.text
    assert any("未知扩展名" in warning for warning in document.warnings)


def test_load_bytes_text_does_not_need_ocr():
    document = load_bytes("姓名：孙七\n".encode("utf-8"), "resume.md")
    assert document.media_type == "text"
    assert "孙七" in document.text
    assert document.engines == ["plain-text"]


def test_load_bytes_rejects_unknown_binary():
    with pytest.raises(ValueError) as excinfo:
        load_bytes(b"\x00\x01\x02\xff\xfe\xfd\x00", "blob.bin")
    assert "无法识别" in str(excinfo.value) or "解码" in str(excinfo.value)


def test_load_bytes_rejects_ole_document():
    with pytest.raises(ValueError) as excinfo:
        load_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64, "old_resume.doc")
    assert "OLE" in str(excinfo.value) or ".docx" in str(excinfo.value)


def test_load_bytes_rejects_empty_and_wrong_type():
    with pytest.raises(ValueError):
        load_bytes(b"", "empty.txt")
    with pytest.raises(TypeError):
        load_bytes("not-bytes", "resume.txt")  # type: ignore[arg-type]


def test_load_document_missing_and_directory(workdir):
    with pytest.raises(FileNotFoundError):
        load_document(workdir / "nope.txt")
    with pytest.raises(IsADirectoryError):
        load_document(workdir)


# ---------------------------------------------------------------------------
# 图片 OCR（注入假引擎）
# ---------------------------------------------------------------------------


def test_load_image_uses_injected_engine_and_normalizes():
    engine = FakeOcrEngine()
    document = load_bytes(make_png(), "resume.png", ocr_engine=engine)
    assert document.media_type == "image"
    assert document.engines == ["fake-ocr"]
    assert len(engine.calls) == 1
    assert "姓名：张晓明" in document.text
    assert "电话：13812345678" in document.text
    assert "邮箱：zhangxm@example.com" in document.text
    assert document.page_count == 1
    assert document.meta["image_format"] == "PNG"
    assert document.meta["frames"] == 1
    assert document.meta["ocr_frames"] == [1]


def test_load_multiframe_tiff_ocr_per_frame():
    engine = FakeOcrEngine(text="姓 名 ： 张 晓 明")
    document = load_bytes(make_multiframe_tiff(), "scan.tif", ocr_engine=engine)
    assert document.media_type == "image"
    assert document.page_count == 2
    assert len(engine.calls) == 2
    assert document.text.count("姓名：张晓明") == 2
    assert document.meta["frames"] == 2
    assert document.meta["ocr_frames"] == [1, 2]


def test_image_extension_content_mismatch_prefers_magic():
    """扩展名写 .txt、内容其实是 PNG：按内容走 OCR 并给出 warning。"""
    engine = FakeOcrEngine(text="姓名：周八")
    document = load_bytes(make_png(), "resume.txt", ocr_engine=engine)
    assert document.media_type == "image"
    assert len(engine.calls) == 1
    assert any("不一致" in warning for warning in document.warnings)


def test_explicit_media_type_alias():
    engine = FakeOcrEngine(text="姓名：吴九")
    document = load_bytes(make_png(), "resume.bin", media_type="image/png", ocr_engine=engine)
    assert document.media_type == "image"
    assert "吴九" in document.text


def test_image_ocr_unavailable_raises():
    with pytest.raises(OcrUnavailableError) as excinfo:
        load_bytes(make_png(), "resume.png", ocr_engine=[])
    assert "OCR" in str(excinfo.value)


def test_image_ocr_empty_result_is_empty_text_with_warning():
    engine = EmptyOcrEngine()
    document = load_bytes(make_png(), "blank.png", ocr_engine=engine)
    assert document.text == ""
    assert document.engines == []
    assert any("未识别到文本" in warning for warning in document.warnings)


def test_ocr_chain_falls_back_to_next_engine():
    broken = BrokenOcrEngine()
    fake = FakeOcrEngine(text="姓名：郑十")
    result = run_ocr_chain(make_png(), [broken, fake], context="图片")
    assert result.engine == "fake-ocr"
    assert result.text == "姓名：郑十"
    assert broken.calls == 1 and len(fake.calls) == 1
    assert any("broken-ocr" in warning for warning in result.warnings)


def test_ocr_chain_all_broken_raises():
    broken = BrokenOcrEngine()
    with pytest.raises(OcrUnavailableError) as excinfo:
        run_ocr_chain(make_png(), [broken])
    assert "所有 OCR 引擎均不可用" in str(excinfo.value)


def test_resolve_engines_accepts_class_and_object():
    engine = FakeOcrEngine()
    assert resolve_engines(engine) == [engine]
    resolved = resolve_engines(FakeOcrEngine)
    assert len(resolved) == 1 and isinstance(resolved[0], FakeOcrEngine)
    with pytest.raises(TypeError):
        resolve_engines(object())


# ---------------------------------------------------------------------------
# OCR 输出归一化
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("姓 名 ： 张 晓 明", "姓名：张晓明"),
        (
            "电 话 ： 13812345678 邮 箱 ： zhangxm@example.com",
            "电话：13812345678 邮箱：zhangxm@example.com",
        ),
        ("技 能 ： PCR 、 生 化 分 析 仪 、 室 内 质 控", "技能：PCR、生化分析仪、室内质控"),
        ("工 作 年 限 ： 3 年", "工作年限：3 年"),
        ("个  人  简  历", "个人简历"),
        ("zhang xm@example.com 张 三", "zhang xm@example.com 张三"),
        ("line1\r\nline2\r\n\r\n\r\n\r\nline3", "line1\nline2\n\nline3"),
        (" 张 晓 明 ", "张晓明"),
        ("a\tb  c", "a b c"),
        ("", ""),
    ],
)
def test_normalize_ocr_text(raw, expected):
    assert normalize_ocr_text(raw) == expected


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


def test_load_pdf_text_layer(workdir):
    path = workdir / "resume.pdf"
    path.write_bytes(make_pdf())
    document = load_document(path)
    assert document.media_type == "pdf"
    assert document.page_count == 1
    assert document.engines == ["pymupdf"]
    assert "张晓明" in document.text
    assert "13812345678" in document.text
    assert document.meta["ocr_pages"] == []
    assert document.meta["pages"][0]["ocr"] is False


def test_load_pdf_bytes_without_path():
    document = load_bytes(make_pdf(), "resume.pdf")
    assert document.media_type == "pdf"
    assert document.source_path == ""
    assert "张晓明" in document.text


def test_load_scanned_pdf_uses_ocr():
    engine = FakeOcrEngine(text="姓 名 ： 钱 一\n电 话 ： 13700000000")
    document = load_bytes(make_pdf(scanned=True), "scan.pdf", ocr_engine=engine)
    assert document.media_type == "pdf"
    assert document.engines == ["fake-ocr"]
    assert document.meta["ocr_pages"] == [1]
    assert "姓名：钱一" in document.text
    assert "电话：13700000000" in document.text
    assert len(engine.calls) == 1


def test_load_mixed_pdf_records_both_engines():
    engine = FakeOcrEngine(text="姓 名 ： 孙 二")
    document = load_bytes(make_mixed_pdf(), "mixed.pdf", ocr_engine=engine)
    assert document.page_count == 2
    assert document.engines == ["pymupdf", "fake-ocr"]
    assert document.meta["ocr_pages"] == [2]
    assert "孙二" in document.text


def test_scanned_pdf_without_engine_raises():
    with pytest.raises(OcrUnavailableError) as excinfo:
        load_bytes(make_pdf(scanned=True), "scan.pdf", ocr_engine=[])
    assert "第 1 页" in str(excinfo.value)


def test_encrypted_pdf_raises_value_error():
    import fitz

    document = fitz.open()
    try:
        page = document.new_page()
        page.insert_text((72, 100), "姓名：李雷 电话：13800000000", fontname="china-s")
        payload = document.tobytes(
            encryption=fitz.PDF_ENCRYPT_AES_256,
            owner_pw="owner",
            user_pw="user",
        )
    finally:
        document.close()
    with pytest.raises(ValueError) as excinfo:
        load_bytes(payload, "secret.pdf")
    assert "加密" in str(excinfo.value)


def test_corrupt_pdf_raises_value_error():
    with pytest.raises(ValueError) as excinfo:
        load_bytes(b"%PDF-1.7\n" + b"garbage garbage garbage\n", "broken.pdf")
    assert "PDF" in str(excinfo.value)


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------


def test_load_docx_paragraphs_and_table_in_document_order():
    payload = make_docx(
        paragraphs=["姓名：李雷", "电话：13800000000"],
        table_rows=[["邮箱", "lilei@example.com"], ["学历", "硕士"]],
    )
    document = load_bytes(payload, "resume.docx")
    assert document.media_type == "docx"
    assert document.engines == ["python-docx"]
    lines = document.text.split("\n")
    assert lines[0] == "姓名：李雷"
    assert lines[1] == "电话：13800000000"
    assert "邮箱 | lilei@example.com" in lines
    assert "学历 | 硕士" in lines
    assert document.meta["paragraphs"] == 2
    assert document.meta["tables"] == 1


def test_load_docx_table_only():
    payload = make_docx(table_rows=[["姓名", "韩梅梅"], ["电话", "13600000000"]])
    document = load_bytes(payload, "table.docx")
    assert "姓名 | 韩梅梅" in document.text
    assert "电话 | 13600000000" in document.text


def test_load_docx_with_embedded_image_falls_back_to_ocr():
    engine = FakeOcrEngine(text="姓 名 ： 李 雷\n电 话 ： 13800000000")
    document = load_bytes(make_image_docx(), "scan.docx", ocr_engine=engine)
    assert document.media_type == "docx"
    assert document.engines == ["fake-ocr"]
    assert len(engine.calls) >= 1
    assert "姓名：李雷" in document.text
    assert document.meta["embedded_images"] >= 1


def test_load_docx_empty_has_warning():
    payload = make_docx(paragraphs=[])
    document = load_bytes(payload, "empty.docx")
    assert document.text == ""
    assert document.engines == []
    assert any("没有可提取的文本" in warning for warning in document.warnings)


def test_load_corrupt_docx_raises_value_error():
    with pytest.raises(ValueError) as excinfo:
        load_bytes(b"PK\x03\x04" + b"\x00" * 64, "broken.docx")
    assert "DOCX" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 真实 Windows OCR（本机已验证；非 Windows / 无 powershell 时跳过）
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not WindowsOcrEngine.available(),
    reason="本机没有 powershell.exe / Windows 内置 OCR",
)
def test_windows_ocr_real_recognition(workdir):
    engine = WindowsOcrEngine()
    assert "windows-ocr" == engine.name
    image_path = workdir / "真实简历.png"
    image_path.write_bytes(
        make_png(
            [
                "个人简历",
                "姓名：张晓明",
                "电话：13812345678",
                "邮箱：zhangxm@example.com",
            ]
        )
    )
    document = load_document(image_path, ocr_engine=engine)
    assert document.engines == ["windows-ocr"]
    assert document.media_type == "image"
    # 真实 OCR 偶有错字（如「检测」→「检氵则」），这里断言稳定字段：
    assert "张晓明" in document.text
    assert "13812345678" in document.text
    assert "zhangxm@example.com" in document.text
    # 行结构必须保留（Windows OCR 按 OcrResult.Lines 输出，字母之间空格被归一化删除）
    assert document.text.split("\n")[0] == "个人简历"


@pytest.mark.skipif(
    not WindowsOcrEngine.available(),
    reason="本机没有 powershell.exe / Windows 内置 OCR",
)
def test_windows_ocr_real_on_scanned_pdf():
    engine = WindowsOcrEngine()
    document = load_bytes(make_pdf(scanned=True), "真实扫描件.pdf", ocr_engine=engine)
    assert document.engines == ["windows-ocr"]
    assert document.meta["ocr_pages"] == [1]
    assert "张晓明" in document.text
