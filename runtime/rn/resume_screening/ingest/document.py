"""把简历文件统一读取成 :class:`DocumentText`。

支持格式与读取策略：

- **纯文本**（``.txt/.md/.json/.csv/.log`` 或内容可解码的未知扩展名）：
  UTF-8（含 BOM）优先，回退 GB18030 / GBK / UTF-16，记录实际编码。
- **PDF**：PyMuPDF（``import fitz``）抽文本层，按 block 坐标恢复视觉阅读
  顺序（招聘平台导出的 PDF 常把内容流写成「条目在前、标题在后」，直接按
  内容流取文本会把经历归到错误段落），并过滤平台水印/追踪串；单页有效
  字符数低于 :data:`MIN_PDF_TEXT_CHARS` 视为扫描页，按
  :data:`PDF_RENDER_SCALE` 渲染成 PNG 后走 OCR，页内的短文本层内容作为
  OCR 失败时的兜底。
- **DOCX**：python-docx，段落与表格按文档顺序拼接（段落一行；表格行内用
  ``" | "`` 连接，合并单元格去重）；若整篇没有任何文字却含内嵌图片，
  则对图片兜底 OCR。
- **图片**（``.png/.jpg/.jpeg/.webp/.bmp/.tif/.tiff/.gif``）：整图 OCR
  （多帧 TIFF 逐帧），不做任何字段抽取。

OCR 引擎链见 :mod:`rn.resume_screening.ingest.ocr`
（Windows 内置 OCR → rapidocr → tesseract）。引擎全部不可用时抛
:class:`OcrUnavailableError`；本层绝不静默返回空字符串冒充成功。

职责边界：只产出「文本 + 引擎信息 + 警告」，不做字段抽取、不做任何判定。
"""

from __future__ import annotations

import codecs
import hashlib
import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import ocr as _ocr
from .ocr import OcrUnavailableError

__all__ = [
    "DocumentText",
    "OcrUnavailableError",
    "load_bytes",
    "load_document",
]


#: PDF 单页文本层有效字符数阈值，低于该值视为扫描页并走 OCR。
MIN_PDF_TEXT_CHARS = 20

#: PDF 扫描页渲染倍率（scale=2.0 约等于 144 DPI，实测 Windows OCR 可用）。
PDF_RENDER_SCALE = 2.0

#: 单页 PDF 文本层超过该字符数才认为「整页都是文本」；20~200 字符且页面含图片时
#: 通常是「页眉文字 + 扫描正文」，必须继续 OCR，否则正文会整页丢失。
MIXED_PDF_TEXT_CHARS = 200

#: PDF 最多读取页数（防止恶意/异常文件让 OCR 跑几个小时）。
MAX_PDF_PAGES = 50

#: 渲染后的最长边像素上限（超出则降低渲染倍率，控制内存与 OCR 耗时）。
MAX_RENDER_EDGE = 4000

#: 单页/单图 OCR 的整体时限（秒）；超时按引擎不可用处理并记 warning。
OCR_TIMEOUT_SECONDS = 90.0

#: 图片分辨率上限（像素）；超出则等比缩到该边长以内再 OCR。
MAX_IMAGE_EDGE = 6000

#: 多帧图片最多识别帧数。
MAX_IMAGE_FRAMES = 20

_IMAGE_EXTENSIONS = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
)
_PDF_EXTENSIONS = frozenset({".pdf"})
_DOCX_EXTENSIONS = frozenset({".docx"})
_TEXT_EXTENSIONS = frozenset(
    {".txt", ".text", ".md", ".markdown", ".json", ".jsonl", ".csv", ".tsv", ".log"}
)

_EXT_TO_TYPE: dict[str, str] = {
    **{ext: "pdf" for ext in _PDF_EXTENSIONS},
    **{ext: "docx" for ext in _DOCX_EXTENSIONS},
    **{ext: "image" for ext in _IMAGE_EXTENSIONS},
    **{ext: "text" for ext in _TEXT_EXTENSIONS},
}

_MEDIA_TYPES = frozenset({"pdf", "docx", "image", "text"})

#: 显式 media_type 允许的别名（含常见 MIME 子类型）。
_MEDIA_TYPE_ALIASES = {
    "pdf": "pdf",
    "application/pdf": "pdf",
    "docx": "docx",
    "word": "docx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "image": "image",
    "img": "image",
    "png": "image",
    "jpg": "image",
    "jpeg": "image",
    "webp": "image",
    "bmp": "image",
    "tif": "image",
    "tiff": "image",
    "gif": "image",
    "text": "text",
    "txt": "text",
    "md": "text",
    "markdown": "text",
    "json": "text",
    "csv": "text",
    "log": "text",
    "text/plain": "text",
}

#: OLE 复合文档（旧版 .doc/.xls/.ppt）魔数，需要给出可执行的错误提示。
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


@dataclass
class DocumentText:
    """一份文件的读取结果（对外契约，字段顺序固定）。"""

    source_path: str
    filename: str
    media_type: str  # "pdf" | "docx" | "image" | "text"
    text: str
    page_count: int
    engines: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """转成普通 dict（便于跨层传递 / 序列化）。"""
        return {
            "source_path": self.source_path,
            "filename": self.filename,
            "media_type": self.media_type,
            "text": self.text,
            "page_count": self.page_count,
            "engines": list(self.engines),
            "warnings": list(self.warnings),
            "meta": dict(self.meta),
        }


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------


def load_document(path: str | Path, *, ocr_engine=None) -> DocumentText:
    """读取本地文件并返回 :class:`DocumentText`。

    ``ocr_engine`` 可注入满足 ``recognize(png_bytes) -> str`` + ``name: str``
    的测试引擎（或引擎列表），注入后不再使用本机默认引擎链。
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"文件不存在：{file_path}")
    if file_path.is_dir():
        raise IsADirectoryError(f"路径是目录而不是文件：{file_path}")
    data = file_path.read_bytes()
    document = load_bytes(data, file_path.name, ocr_engine=ocr_engine)
    document.source_path = str(file_path.resolve())
    return document


def load_bytes(
    data: bytes,
    filename: str,
    *,
    media_type: str | None = None,
    ocr_engine=None,
) -> DocumentText:
    """读取内存中的文件字节并返回 :class:`DocumentText`。

    ``filename`` 用于扩展名判断与回落展示；``media_type`` 显式指定时优先于
    扩展名与 magic bytes（仍会记录不一致的 warning）。
    """
    if isinstance(data, memoryview):
        data = data.tobytes()
    elif isinstance(data, bytearray):
        data = bytes(data)
    if not isinstance(data, bytes):
        raise TypeError(f"data 必须是 bytes，收到 {type(data)!r}")
    if not data:
        raise ValueError("文件内容为空（0 字节），无法识别")

    name = str(filename or "")
    suffix = Path(name).suffix.lower()
    base_meta: dict = {
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    resolved, warnings = _resolve_media_type(data, suffix, media_type)

    if resolved == "text":
        text, encoding = _decode_text(data)
        return DocumentText(
            source_path="",
            filename=name,
            media_type="text",
            text=_normalize_newlines(text),
            page_count=1,
            engines=["plain-text"],
            warnings=warnings,
            meta={**base_meta, "encoding": encoding},
        )
    if resolved == "pdf":
        return _load_pdf(data, name, base_meta, warnings, ocr_engine)
    if resolved == "docx":
        return _load_docx(data, name, base_meta, warnings, ocr_engine)
    if resolved == "image":
        return _load_image(data, name, base_meta, warnings, ocr_engine)
    raise AssertionError(f"未处理的媒体类型：{resolved}")  # pragma: no cover


# ---------------------------------------------------------------------------
# 媒体类型判定
# ---------------------------------------------------------------------------


def _normalize_media_type(value: str) -> str | None:
    key = str(value).strip().lower()
    if "/" in key:
        key = key.split(";", 1)[0].strip()
    resolved = _MEDIA_TYPE_ALIASES.get(key)
    if resolved is None and key.startswith("image/"):
        resolved = "image"
    if resolved is None and key.startswith("text/"):
        resolved = "text"
    return resolved


def _type_from_magic(data: bytes) -> str | None:
    head = data[:16]
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image"
    if head.startswith(b"\xff\xd8\xff"):
        return "image"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return "image"
    if head.startswith(b"II*\x00") or head.startswith(b"MM\x00*"):
        return "image"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image"
    if head.startswith(b"PK\x03\x04"):
        return "docx"
    return None


def _resolve_media_type(
    data: bytes, suffix: str, media_type: str | None
) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if data.startswith(_OLE_MAGIC):
        raise ValueError(
            "不支持旧版 OLE 复合文档（.doc/.xls/.ppt 等），请另存为 .docx 或 PDF 后再上传"
        )
    if media_type is not None:
        explicit = _normalize_media_type(media_type)
        if explicit is None:
            raise ValueError(f"不支持的 media_type：{media_type!r}")
        magic = _type_from_magic(data)
        if magic is not None and magic != explicit:
            warnings.append(
                f"显式 media_type={explicit} 与文件内容实例（{magic}）不一致，按 {explicit} 处理"
            )
        return explicit, warnings

    extension_type = _EXT_TO_TYPE.get(suffix)
    magic_type = _type_from_magic(data)
    if extension_type is not None and magic_type is not None:
        if extension_type != magic_type:
            warnings.append(
                f"扩展名 {suffix} 与文件内容不一致（内容像 {magic_type}），按内容类型处理"
            )
            return magic_type, warnings
        return extension_type, warnings
    if extension_type is not None:
        return extension_type, warnings
    if magic_type is not None:
        warnings.append(f"未知扩展名 {suffix or '(无)'}，按文件内容识别为 {magic_type}")
        return magic_type, warnings
    try:
        _decode_text(data)
    except ValueError as exc:
        raise ValueError(
            f"无法识别的文件类型（扩展名 {suffix or '(无)'}，且不是可解码文本）"
        ) from exc
    warnings.append(f"未知扩展名 {suffix or '(无)'}，按纯文本读取")
    return "text", warnings


# ---------------------------------------------------------------------------
# 纯文本
# ---------------------------------------------------------------------------


def _looks_like_text(text: str) -> bool:
    """判断解码结果像不像文本：拒绝 NUL（无 BOM UTF-16）与大量控制字符。

    GB18030 对任意字节几乎都能解码成功，所以「能解码」不能作为文本判据；
    二进制文件（例如把 BMP 当未知扩展名上传）必须在这里被挡住。
    """
    if not text.strip():
        return False
    sample = text[:4000]
    if any(ord(ch) < 9 for ch in sample) or "\x00" in sample:
        return False
    controls = sum(1 for ch in sample if 0x0E <= ord(ch) <= 0x1F or 0x7F <= ord(ch) <= 0x9F)
    return controls <= max(2, len(sample) // 100)


def _decode_text(data: bytes) -> tuple[str, str]:
    """按 UTF-8(BOM) → UTF-16 → GB18030 → GBK 顺序解码，返回 (文本, 编码名)。"""
    if data.startswith(codecs.BOM_UTF8):
        try:
            return data.decode("utf-8-sig"), "utf-8-sig"
        except UnicodeDecodeError:
            pass
    if data.startswith(codecs.BOM_UTF16_LE) or data.startswith(codecs.BOM_UTF16_BE):
        try:
            return data.decode("utf-16"), "utf-16"
        except UnicodeDecodeError:
            pass
    # 无 BOM 的 UTF-16（Windows 记事本「Unicode」另存）含大量 NUL，
    # 先按 UTF-16 试一次，否则会被 gb18030 解成乱码且不报错。
    if b"\x00" in data[:512]:
        for encoding in ("utf-16-le", "utf-16-be"):
            try:
                candidate = data.decode(encoding)
            except UnicodeDecodeError:
                continue
            if _looks_like_text(candidate):
                return candidate, encoding
    for encoding in ("utf-8", "gb18030", "gbk"):
        try:
            candidate = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if not _looks_like_text(candidate):
            continue
        return candidate, encoding
    raise ValueError("文件内容无法按 UTF-8 / GB18030 解码为文本（疑似二进制文件）")


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


# ---------------------------------------------------------------------------
# PDF：阅读顺序与水印过滤
# ---------------------------------------------------------------------------

#: 平台水印/追踪串的最短长度；低于该长度不可能是水印，避免误伤真实编号。
WATERMARK_MIN_LEN = 16

#: 水印串允许的字符集（BOSS 直聘等平台插入的 base62 追踪串，常以 ``~~`` 结尾）。
_WATERMARK_CHARS_RE = re.compile(r"[0-9A-Za-z_~\-]+")


def _is_watermark_line(line: str) -> bool:
    """判断一行是否是招聘平台水印/追踪串。

    BOSS 直聘等平台导出的 PDF 会在文本层插入形如
    ``04a1170a2e227ad01Hd83Ny6EVFRwou6V_iYWOGnnfLSNRVm~~`` 的追踪串。
    这里只认「整行无中文、无空格、无 @/URL、仅字母数字与 ``_~-``、长度
    >= :data:`WATERMARK_MIN_LEN`」的行，``SOP-2021-001`` 这类短编号、
    邮箱、URL 都不会被误删。
    """
    text = line.strip()
    if len(text) < WATERMARK_MIN_LEN:
        return False
    if "@" in text or "://" in text or " " in text:
        return False
    if not _WATERMARK_CHARS_RE.fullmatch(text):
        return False
    return any(ch.isdigit() for ch in text)


def _drop_watermark_lines(text: str) -> str:
    """删掉文本中的平台水印行（保留其余行与原有顺序）。"""
    return "\n".join(
        line for line in _normalize_newlines(text).splitlines() if not _is_watermark_line(line)
    )


def _find_column_split(
    blocks: list[tuple[float, float, float, float, str]],
    width: float,
) -> float | None:
    """在单页里找一条「空白竖缝」，把左右两栏分开。

    只有左右两侧各有至少 3 个 block、竖缝至少 8pt 宽、且两栏都不窄于页宽的
    18% 时才认为是真正的双栏排版（左栏窄条通常是侧边栏）。日期栏这类窄
    竖排（< 18% 页宽）不按双栏处理，交给逐行排序，让日期与同行的条目相邻。
    """
    if width <= 0:
        return None
    half_gap = 4.0
    min_side = 3
    min_column_width = width * 0.18
    start = width * 0.18
    stop = width * 0.82
    step = 2.0
    candidate = start
    while candidate <= stop:
        crosses = any(
            b[0] < candidate + half_gap and b[2] > candidate - half_gap for b in blocks
        )
        if not crosses:
            left = [b for b in blocks if b[2] <= candidate + half_gap]
            right = [b for b in blocks if b[0] >= candidate - half_gap]
            if len(left) >= min_side and len(right) >= min_side:
                left_edge = min(b[0] for b in left)
                left_width = max(b[2] for b in left) - left_edge
                right_width = width - min(b[0] for b in right)
                if left_width >= min_column_width and right_width >= min_column_width:
                    return candidate
        candidate += step
    return None


def _pdf_page_text(page) -> str:
    """按视觉阅读顺序拼一页 PDF 的文本层。

    直接 ``page.get_text("text")`` 是按 PDF 内容流（绘制顺序）输出；招聘平台
    简历的绘制顺序常常是「教育/工作条目 → 章节标题」，会让章节解析把条目
    归到上一段。这里改成用 ``get_text("blocks")`` 的坐标排序：

    - 先丢掉页外绘制的 block（旋转水印/装饰）和平台水印行；
    - 双栏页（左窄侧边栏 + 右正文）先左栏后右栏，各栏内按 (y, x)；
    - 单栏页直接按 (y, x)，近似人类自上而下、自左而右的阅读顺序。
    """
    try:
        raw_blocks = page.get_text("blocks")
    except Exception:
        return _drop_watermark_lines(page.get_text("text") or "")

    rect = page.rect
    width = float(rect.width or 0.0)
    height = float(rect.height or 0.0)
    blocks: list[tuple[float, float, float, float, str]] = []
    for item in raw_blocks:
        try:
            x0, y0, x1, y1, raw_text, _bno, btype = item
        except (TypeError, ValueError):
            continue
        if int(btype) != 0:
            continue
        lines = [
            line.strip()
            for line in _normalize_newlines(str(raw_text)).splitlines()
            if line.strip()
        ]
        if not lines:
            continue
        if (
            x0 < -1.5
            or y0 < -1.5
            or (width > 0 and x1 > width + 1.5)
            or (height > 0 and y1 > height + 1.5)
        ):
            continue
        kept = [line for line in lines if not _is_watermark_line(line)]
        if not kept:
            continue
        blocks.append((float(x0), float(y0), float(x1), float(y1), "\n".join(kept)))
    if not blocks:
        return _drop_watermark_lines(page.get_text("text") or "")

    def _row_key(block: tuple[float, float, float, float, str]) -> tuple[float, float]:
        return (round(block[1], 1), round(block[0], 1))

    split = _find_column_split(blocks, width)
    if split is None:
        ordered = sorted(blocks, key=_row_key)
    else:
        left = sorted((b for b in blocks if b[2] <= split + 1.0), key=_row_key)
        right = sorted((b for b in blocks if b[0] >= split - 1.0), key=_row_key)
        middle = [b for b in blocks if b not in left and b not in right]
        ordered = left + sorted(middle, key=_row_key) + right
    return "\n".join(block[4] for block in ordered)


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


def _load_pdf(
    data: bytes,
    filename: str,
    base_meta: dict,
    warnings: list[str],
    ocr_engine,
) -> DocumentText:
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:  # pragma: no cover - 环境已安装
        raise RuntimeError("PDF 读取需要 PyMuPDF（import fitz）") from exc

    try:
        pdf = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"PDF 打开失败：{exc}") from exc
    try:
        if pdf.needs_pass:
            raise ValueError("PDF 已加密，需要密码才能读取文本")
        page_count = int(pdf.page_count or 0)
        if page_count == 0:
            warnings.append("PDF 没有任何页面")
        limit = page_count
        if page_count > MAX_PDF_PAGES:
            limit = MAX_PDF_PAGES
            warnings.append(
                f"PDF 共 {page_count} 页，超过读取上限 {MAX_PDF_PAGES} 页，只读取前 {MAX_PDF_PAGES} 页"
            )
        engine_list = None
        engines: list[str] = []
        text_pages = 0
        ocr_pages: list[int] = []
        details: list[dict] = []
        page_texts: list[str] = []

        for index in range(limit):
            page = pdf.load_page(index)
            try:
                raw_text = _pdf_page_text(page)
            except Exception as exc:
                warnings.append(f"PDF 第 {index + 1} 页文本层读取失败：{exc}")
                raw_text = ""
            page_text = _normalize_newlines(raw_text).strip()
            try:
                image_count = len(page.get_images(full=False) or [])
            except Exception:
                image_count = 0
            detail: dict = {
                "index": index + 1,
                "chars": len(page_text),
                "images": image_count,
                "ocr": False,
                "engine": None,
            }

            # 只有「文本层足够多」且（无图片 或 文本已覆盖整页）才跳过 OCR。
            # 「页眉 36 个字的文字层 + 整页扫描正文」必须继续 OCR，否则正文丢失。
            text_only_page = len(page_text) >= MIN_PDF_TEXT_CHARS and (
                not image_count or len(page_text) >= MIXED_PDF_TEXT_CHARS
            )
            if text_only_page:
                page_texts.append(page_text)
                text_pages += 1
                details.append(detail)
                continue

            # 文本层过少（或文本层+图片的混合页）→ 渲染成 PNG 后 OCR。
            try:
                rect = page.rect
                longest = max(float(rect.width), float(rect.height), 1.0)
                zoom = min(PDF_RENDER_SCALE, MAX_RENDER_EDGE / longest)
                zoom = max(0.5, zoom)
                pixmap = page.get_pixmap(
                    matrix=fitz.Matrix(zoom, zoom),
                    alpha=False,
                )
                png = pixmap.tobytes("png")
            except Exception as exc:
                warnings.append(f"PDF 第 {index + 1} 页渲染失败，跳过 OCR：{exc}")
                if page_text:
                    page_texts.append(page_text)
                    text_pages += 1
                detail["error"] = f"render: {exc}"
                details.append(detail)
                continue

            if engine_list is None:
                engine_list = _ocr.resolve_engines(ocr_engine)
            try:
                result = _ocr.run_ocr_chain(
                    png,
                    engine_list,
                    context=f"PDF 第 {index + 1} 页",
                    timeout=OCR_TIMEOUT_SECONDS,
                )
            except OcrUnavailableError as exc:
                raise OcrUnavailableError(
                    f"PDF 第 {index + 1} 页没有有效文本层，需要 OCR，但{exc}"
                ) from exc
            warnings.extend(result.warnings)
            if result.engine and result.engine not in engines:
                engines.append(result.engine)
            ocr_text = _drop_watermark_lines(result.text).strip()
            if ocr_text:
                # 混合页保留文本层内容，再接 OCR 正文。
                combined = "\n".join(part for part in (page_text, ocr_text) if part)
                page_texts.append(combined)
                if page_text:
                    text_pages += 1
                ocr_pages.append(index + 1)
                detail.update({"ocr": True, "chars": len(combined), "engine": result.engine})
            else:
                warnings.append(f"PDF 第 {index + 1} 页 OCR 未识别到文本")
                if page_text:
                    page_texts.append(page_text)
                    text_pages += 1
            details.append(detail)

        final_engines = list(engines)
        if text_pages:
            final_engines.insert(0, "pymupdf")
        return DocumentText(
            source_path="",
            filename=filename,
            media_type="pdf",
            text="\n".join(page_texts),
            page_count=page_count,
            engines=final_engines,
            warnings=warnings,
            meta={
                **base_meta,
                "ocr_pages": ocr_pages,
                "pages": details,
            },
        )
    finally:
        pdf.close()


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------


def _load_docx(
    data: bytes,
    filename: str,
    base_meta: dict,
    warnings: list[str],
    ocr_engine,
) -> DocumentText:
    try:
        from docx import Document as DocxDocument
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as exc:  # pragma: no cover - 环境已安装
        raise RuntimeError("DOCX 读取需要 python-docx") from exc

    try:
        docx = DocxDocument(io.BytesIO(data))
    except Exception as exc:
        raise ValueError(f"DOCX 打开失败：{exc}") from exc

    parts: list[str] = []
    paragraph_count = 0
    table_count = 0
    body = docx.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            text = Paragraph(child, docx).text.strip()
            if text:
                parts.append(_normalize_newlines(text))
                paragraph_count += 1
        elif child.tag == qn("w:tbl"):
            table_count += 1
            rows = _table_rows(Table(child, docx))
            if rows:
                parts.append("\n".join(rows))

    # 文本框（w:txbxContent）不在 body 的段落/表格序列里，正文循环拿不到；
    # 简历模板常把「姓名/电话」放在文本框，必须单独收集，否则字段全丢。
    seen_textboxes: set[str] = set()
    try:
        for box in body.iter(qn("w:txbxContent")):
            box_text = "".join(node.text or "" for node in box.iter(qn("w:t"))).strip()
            if box_text and box_text not in seen_textboxes:
                seen_textboxes.add(box_text)
                parts.append(_normalize_newlines(box_text))
    except Exception as exc:  # 损坏的 XML 不应让整份简历失败
        warnings.append(f"DOCX 文本框读取失败：{exc}")

    text = "\n".join(parts)
    has_text_layer = bool(text.strip())
    ocr_engines: list[str] = []
    images: list[bytes] = []
    if not has_text_layer:
        images = _docx_embedded_images(data, warnings)
        if images:
            engine_list = _ocr.resolve_engines(ocr_engine)
            ocr_texts: list[str] = []
            for position, png in enumerate(images, start=1):
                try:
                    result = _ocr.run_ocr_chain(
                        png,
                        engine_list,
                        context=f"DOCX 内嵌图片 {position}",
                        timeout=OCR_TIMEOUT_SECONDS,
                    )
                except OcrUnavailableError as exc:
                    raise OcrUnavailableError(
                        f"DOCX 没有文本层，需要 OCR 内嵌图片，但{exc}"
                    ) from exc
                warnings.extend(result.warnings)
                if result.engine and result.engine not in ocr_engines:
                    ocr_engines.append(result.engine)
                if result.text.strip():
                    ocr_texts.append(result.text.strip())
            text = "\n".join(ocr_texts)
        elif not parts:
            warnings.append("DOCX 中没有可提取的文本（无段落、表格或内嵌图片）")

    engines = (["python-docx"] if has_text_layer else []) + ocr_engines
    return DocumentText(
        source_path="",
        filename=filename,
        media_type="docx",
        text=text,
        page_count=1,
        engines=engines,
        warnings=warnings,
        meta={
            **base_meta,
            "paragraphs": paragraph_count,
            "tables": table_count,
            "embedded_images": len(images),
        },
    )


def _table_rows(table) -> list[str]:
    """表格 → 每行一个字符串，单元格用 " | " 连接（合并单元格只出现一次）。"""
    rows: list[str] = []
    for row in table.rows:
        seen: set = set()
        cells: list[str] = []
        for cell in row.cells:
            key = cell._tc  # 合并单元格会复用同一个 <w:tc> 元素
            if key in seen:
                continue
            seen.add(key)
            cell_text = " ".join(
                paragraph.text.strip()
                for paragraph in cell.paragraphs
                if paragraph.text.strip()
            )
            if cell_text:
                cells.append(cell_text)
        if cells:
            rows.append(" | ".join(cells))
    return rows


def _docx_embedded_images(data: bytes, warnings: list[str]) -> list[bytes]:
    """把 DOCX 内嵌图片（word/media/*）转成 PNG 字节列表，供 OCR 兜底。"""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - 环境已安装
        return []
    images: list[bytes] = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            media_names = sorted(
                name
                for name in archive.namelist()
                if name.startswith("word/media/") and not name.endswith("/")
            )
            for name in media_names:
                try:
                    image = Image.open(io.BytesIO(archive.read(name)))
                    image.load()
                    images.append(_to_png_bytes(image))
                except Exception as exc:
                    warnings.append(f"DOCX 内嵌图片 {name} 无法解码：{exc}")
    except (zipfile.BadZipFile, OSError) as exc:
        warnings.append(f"DOCX 内嵌图片读取失败：{exc}")
    return images


# ---------------------------------------------------------------------------
# 图片
# ---------------------------------------------------------------------------


def _load_image(
    data: bytes,
    filename: str,
    base_meta: dict,
    warnings: list[str],
    ocr_engine,
) -> DocumentText:
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - 环境已安装
        raise RuntimeError("图片读取需要 Pillow") from exc

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:
        raise ValueError(f"图片打开失败：{exc}") from exc
    image_format = image.format
    image_size = list(image.size)
    frame_count = int(getattr(image, "n_frames", 1) or 1)
    if frame_count > MAX_IMAGE_FRAMES:
        warnings.append(
            f"图片共 {frame_count} 帧，超过识别上限 {MAX_IMAGE_FRAMES} 帧，只识别前 {MAX_IMAGE_FRAMES} 帧"
        )
        frame_count = MAX_IMAGE_FRAMES
    # 超大图先等比缩小，避免 OCR 引擎内存溢出 / 识别超时。
    if max(image_size) > MAX_IMAGE_EDGE:
        ratio = MAX_IMAGE_EDGE / max(image_size)
        new_size = (max(1, int(image_size[0] * ratio)), max(1, int(image_size[1] * ratio)))
        warnings.append(f"图片尺寸 {image_size} 超过上限，已缩放到 {list(new_size)} 后识别")
        image = image.resize(new_size)
        image_size = list(image.size)
    engine_list = _ocr.resolve_engines(ocr_engine)
    engines: list[str] = []
    texts: list[str] = []
    ocr_frames: list[int] = []
    for index in range(frame_count):
        if frame_count > 1:
            image.seek(index)
        try:
            png = _to_png_bytes(image)
        except Exception as exc:
            warnings.append(f"图片第 {index + 1} 帧转换 PNG 失败：{exc}")
            continue
        context = f"图片第 {index + 1} 帧" if frame_count > 1 else "图片"
        try:
            result = _ocr.run_ocr_chain(
                png,
                engine_list,
                context=context,
                timeout=OCR_TIMEOUT_SECONDS,
            )
        except OcrUnavailableError as exc:
            raise OcrUnavailableError(f"图片需要 OCR，但{exc}") from exc
        warnings.extend(result.warnings)
        if result.engine and result.engine not in engines:
            engines.append(result.engine)
        if result.text.strip():
            texts.append(result.text)
            ocr_frames.append(index + 1)
        else:
            warnings.append(f"图片第 {index + 1} 帧未识别到文本")
    return DocumentText(
        source_path="",
        filename=filename,
        media_type="image",
        text="\n".join(texts),
        page_count=frame_count,
        engines=engines,
        warnings=warnings,
        meta={
            **base_meta,
            "image_format": image_format,
            "image_size": image_size,
            "frames": frame_count,
            "ocr_frames": ocr_frames,
        },
    )


def _to_png_bytes(image) -> bytes:
    """任意 Pillow 图像帧 → PNG 字节（OCR 引擎协议统一收 PNG）。"""
    frame = image.convert("RGB") if image.mode != "RGB" else image
    buffer = io.BytesIO()
    frame.save(buffer, format="PNG")
    return buffer.getvalue()
