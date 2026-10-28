"""OCR 引擎层：可插拔的识别引擎 + Windows OCR 输出归一化。

职责边界（与 ``document.py`` 的分工）：
- 本模块只做「一段图片字节 → 文本」，不知道 PDF / DOCX 的存在；
- 引擎优先级固定为 Windows 内置 OCR → rapidocr → tesseract，全部不可用时
  抛 :class:`OcrUnavailableError`，绝不静默返回空字符串；
- Windows OCR 会在每个 CJK 字符之间插空格（实测「姓 名 ： 张 晓 明」），
  因此识别结果统一经过 :func:`normalize_ocr_text` 归一化；
- ``ocr_engine=`` 注入对象只需满足 ``name: str`` + ``recognize(png_bytes) -> str``
  （也接受 ``[engine, ...]`` 列表，按顺序尝试），方便测试与替换实现。

单独使用示例::

    from rn.resume_screening.ingest.ocr import run_ocr_chain

    result = run_ocr_chain(png_bytes)
    print(result.engine, result.text)
"""

from __future__ import annotations

import importlib.util
import io
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, Sequence, runtime_checkable

logger = logging.getLogger(__name__)

__all__ = [
    "OcrEngine",
    "OcrRunResult",
    "OcrUnavailableError",
    "RapidOcrEngine",
    "TesseractEngine",
    "WindowsOcrEngine",
    "build_default_engines",
    "normalize_ocr_text",
    "resolve_engines",
    "run_ocr_chain",
]


class OcrUnavailableError(RuntimeError):
    """没有任何可用的 OCR 引擎，或某个引擎在本机完全无法工作。"""


@runtime_checkable
class OcrEngine(Protocol):
    """OCR 引擎协议（鸭子类型）：``name`` + ``recognize(png_bytes) -> str``。"""

    name: str

    def recognize(self, png_bytes: bytes) -> str:  # pragma: no cover - protocol
        ...


@dataclass
class OcrRunResult:
    """一次链式识别的结果。"""

    text: str = ""
    engine: str | None = None
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Windows OCR 输出归一化
# ---------------------------------------------------------------------------

# Windows OCR 把中文按「字」切分并在每字之间插入空格：
#   「姓 名 ： 张 晓 明」→「姓名：张晓明」
# CJK 字符集合：CJK 标点（、。：等）、扩展 A、基本区、兼容区、全角字符区。
# 注意故意排除 U+3000（全角空格，先当普通空格处理）。
_CJK = r"\u3001-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef"
# CJK 标点/全角符号（不含汉字本身），用于收拾标点两侧的空格。
_CJK_PUNCT = (
    r"\u3001-\u303f\uff01-\uff0f\uff1a-\uff20\uff3b-\uff40\uff5b-\uff65"
    r"\u2018\u2019\u201c\u201d\u2026\u2014"
)
# 用 lookaround 而不是「消费字符」的替换：一次扫描就能处理
# 「张 晓 明」这种奇数长度的拆分，不会因为逐个消费而漏掉最后一对。
_CJK_GAP = re.compile(rf"(?<=[{_CJK}])[ \t]+(?=[{_CJK}])")
_SPACE_BEFORE_PUNCT = re.compile(rf"[ \t]+(?=[{_CJK_PUNCT}])")
_SPACE_AFTER_PUNCT = re.compile(rf"(?<=[{_CJK_PUNCT}])[ \t]+")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")
_MULTI_NEWLINE = re.compile(r"\n{3,}")

# OCR 常见「形近字/拆分」修复（只做保守的、上下文可判定的替换）：
# - 数字被识别成字母：1381234S678 → 13812345678
# - 日期/区间分隔符被识别成中文「一」：2021．07 一 2024．08 → 2021.07-2024.08
# - 数字之间被逐字插空格：1 0 一 1 2 K → 10-12K
# - 短拉丁缩写被拆开：PC R → PCR
_FULLWIDTH_DOT = re.compile(r"[．。](?=\d)")
_DIGIT_GAP = re.compile(r"(?<=\d)[ \t]+(?=\d)")
_DATE_DASH = re.compile(r"(?<=\d)[ \t]*[一—–~～至][ \t]*(?=\d)")
_DIGIT_CONFUSION = re.compile(r"(?<=\d)[SOoIlB](?=\d)")
_DIGIT_CONFUSION_MAP = {"S": "5", "O": "0", "o": "0", "I": "1", "l": "1", "B": "8"}
_SHORT_LATIN_GAP = re.compile(r"(?<![A-Za-z])([A-Z]{1,2})[ \t]+([A-Z]{1,2})(?![A-Za-z])")


def _repair_ocr_confusions(text: str) -> str:
    """修复 OCR 的拆分与形近字（只处理数字/日期/短缩写上下文，避免误改英文单词）。"""
    value = _FULLWIDTH_DOT.sub(".", text)
    previous = None
    while previous != value:  # 「P C R」「1 0 一 1 2」需要反复折叠
        previous = value
        value = _DIGIT_GAP.sub("", value)
        value = _SHORT_LATIN_GAP.sub(r"\1\2", value)
    value = _DATE_DASH.sub("-", value)
    value = _DIGIT_CONFUSION.sub(
        lambda match: _DIGIT_CONFUSION_MAP.get(match.group(0), match.group(0)), value,
    )
    return value


def normalize_ocr_text(text: str) -> str:
    """归一化 OCR 原始输出（去 CJK 间空格、收拾标点两侧空格、统一换行）。

    规则：
    - 两个 CJK 字符之间的空格删除：``姓 名`` → ``姓名``、``张 晓 明`` → ``张晓明``；
    - CJK 标点（：、，。等）两侧空格删除：``电话 ： 138…`` → ``电话：138…``；
    - CJK 与 ASCII / 数字之间保留一个空格：``3 年`` 不变；
    - 英文单词之间的空格原样保留：``zhang xm@example.com`` 不变；
    - 全角空格 U+3000 转普通空格，
      每行去首尾空白、行内多余空格压成一个，最多保留一个空行；
    - 额外修复 OCR 常见拆分与形近字：数字间空格、日期分隔符「一」、
      数字被识别成 S/O/I/l/B、短拉丁缩写被拆开（``PC R`` → ``PCR``）。
    """
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u3000", " ").replace("\t", " ")
    text = _SPACE_BEFORE_PUNCT.sub("", text)
    text = _SPACE_AFTER_PUNCT.sub("", text)
    text = _CJK_GAP.sub("", text)
    text = _repair_ocr_confusions(text)
    lines = [
        _MULTI_SPACE.sub(" ", line).strip()
        for line in text.split("\n")
    ]
    text = "\n".join(lines)
    text = _MULTI_NEWLINE.sub("\n\n", text)
    return text.strip("\n")


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------


def _make_temp_dir(prefix: str) -> str:
    """创建临时目录。

    故意不用 ``tempfile.mkdtemp``：本机沙箱里 ``mode=0o700`` 的目录无法被
    同一受限 token 再次枚举，直接用 0o777 规避。
    """
    base = tempfile.gettempdir()
    path = os.path.join(base, f"{prefix}{uuid.uuid4().hex}")
    os.makedirs(path, mode=0o777, exist_ok=True)
    return path


def _decode_output(raw: bytes | None) -> str:
    """解码子进程输出：优先 UTF-8，回退 gb18030 / cp936。"""
    data = raw or b""
    if not data:
        return ""
    for encoding in ("utf-8", "gb18030", "cp936"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _powershell_path() -> str | None:
    """Windows PowerShell 5.1 可执行文件路径（pwsh 7 下 WinRT 不可靠）。"""
    if sys.platform != "win32":
        return None
    return shutil.which("powershell.exe") or shutil.which("powershell")


# ---------------------------------------------------------------------------
# 引擎实现：Windows 内置 OCR（首选）
# ---------------------------------------------------------------------------


class WindowsOcrEngine:
    """通过 Windows PowerShell 5.1 调用 WinRT ``Windows.Media.Ocr``。

    已在本机验证可用：``powershell.exe -NoProfile -ExecutionPolicy Bypass
    -File _windows_ocr.ps1 -Path <图片>``。识别结果按 ``OcrResult.Lines``
    逐行输出，以保留简历的行结构。
    """

    name = "windows-ocr"

    #: -Probe 探测结果缓存（每进程只探测一次）。
    _AVAILABLE_CACHE: bool | None = None

    def __init__(
        self,
        *,
        timeout: float = 60.0,
        ps1_path: str | Path | None = None,
    ) -> None:
        self.timeout = float(timeout)
        self._ps1_path = Path(ps1_path) if ps1_path is not None else None

    @classmethod
    def available(cls) -> bool:
        """真探测：powershell + 桥脚本 + OCR 识别语言三者都就绪才算可用。

        只查 ``powershell.exe`` 存在会把「系统没装中文 OCR 语言包」误判为可用，
        然后在第一份扫描件上失败。
        """
        if cls._AVAILABLE_CACHE is not None:
            return cls._AVAILABLE_CACHE
        exe = _powershell_path()
        ok = False
        if exe is not None:
            script = Path(__file__).with_name("_windows_ocr.ps1")
            if script.is_file():
                try:
                    proc = subprocess.run(
                        [
                            exe,
                            "-NoProfile",
                            "-NonInteractive",
                            "-ExecutionPolicy",
                            "Bypass",
                            "-File",
                            str(script),
                            "-Probe",
                        ],
                        capture_output=True,
                        timeout=30,
                        check=False,
                    )
                    ok = proc.returncode == 0 and b"OK" in (proc.stdout or b"")
                except (OSError, subprocess.TimeoutExpired) as exc:
                    logger.debug("windows-ocr 探测失败：%s", exc)
                    ok = False
        cls._AVAILABLE_CACHE = ok
        return ok

    @property
    def ps1_path(self) -> Path:
        return self._ps1_path or Path(__file__).with_name("_windows_ocr.ps1")

    def recognize(self, png_bytes: bytes) -> str:
        if not png_bytes:
            raise OcrUnavailableError("windows-ocr 收到空图片字节")
        exe = _powershell_path()
        if exe is None:
            raise OcrUnavailableError("找不到 powershell.exe（Windows PowerShell 5.1）")
        script = self.ps1_path
        if not script.is_file():
            raise OcrUnavailableError(f"缺少 Windows OCR 桥脚本：{script}")

        workdir = _make_temp_dir("rn-ocr-")
        try:
            image_path = os.path.join(workdir, "page.png")
            with open(image_path, "wb") as handle:
                handle.write(png_bytes)
            cmd = [
                exe,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-Path",
                image_path,
            ]
            logger.debug("windows-ocr: %s", cmd)
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=self.timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise OcrUnavailableError(
                    f"windows-ocr 识别超时（{self.timeout:g}s）"
                ) from exc
            stdout = _decode_output(proc.stdout)
            stderr = _decode_output(proc.stderr).strip()
            if proc.returncode != 0:
                detail = stderr or stdout.strip() or "(无输出)"
                raise OcrUnavailableError(
                    f"windows-ocr 退出码 {proc.returncode}：{detail}"
                )
            return stdout.lstrip("\ufeff").strip("\r\n")
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 引擎实现：rapidocr / tesseract（备选，装好即生效）
# ---------------------------------------------------------------------------


def _module_available(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


class RapidOcrEngine:
    """rapidocr（含 onnxruntime 后端）封装；本机未安装时 ``available()`` 为假。"""

    name = "rapidocr"

    def __init__(self) -> None:
        self._engine = None

    @staticmethod
    def available() -> bool:
        return _module_available("rapidocr") or _module_available("rapidocr_onnxruntime")

    def _load(self):
        if self._engine is None:
            try:
                from rapidocr import RapidOCR  # type: ignore[import-not-found]
            except ImportError:
                from rapidocr_onnxruntime import RapidOCR  # type: ignore[import-not-found]
            self._engine = RapidOCR()
        return self._engine

    def recognize(self, png_bytes: bytes) -> str:
        try:
            import numpy as np
            from PIL import Image
        except ImportError as exc:
            raise OcrUnavailableError(f"rapidocr 依赖缺失：{exc}") from exc
        try:
            engine = self._load()
        except Exception as exc:
            raise OcrUnavailableError(f"rapidocr 初始化失败：{exc}") from exc
        try:
            image = Image.open(io.BytesIO(png_bytes)).convert("RGB")
            array = np.array(image)
            output = engine(array)
        except Exception as exc:
            raise OcrUnavailableError(f"rapidocr 识别失败：{exc}") from exc
        return _extract_rapidocr_text(output)


def _extract_rapidocr_text(output) -> str:
    """兼容 rapidocr 新旧两种返回结构。"""
    texts: list[str] = []
    payload = output
    if isinstance(output, tuple) and len(output) == 2:
        payload = output[0]
    if payload is None:
        return ""
    txts = getattr(payload, "txts", None)
    if txts is not None:
        texts = [str(item) for item in txts if item]
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                candidate = item[1]
                if isinstance(candidate, str) and candidate:
                    texts.append(candidate)
            elif isinstance(item, str) and item:
                texts.append(item)
    elif isinstance(payload, str):
        texts = [payload]
    return "\n".join(texts)


class TesseractEngine:
    """pytesseract 封装；要求 ``tesseract`` 可执行文件真实存在。"""

    name = "tesseract"

    @staticmethod
    def available() -> bool:
        return shutil.which("tesseract") is not None and _module_available("pytesseract")

    def recognize(self, png_bytes: bytes) -> str:
        try:
            import io

            import pytesseract  # type: ignore[import-not-found]
            from PIL import Image
        except ImportError as exc:
            raise OcrUnavailableError(f"tesseract 依赖缺失：{exc}") from exc
        image = Image.open(io.BytesIO(png_bytes))
        try:
            return pytesseract.image_to_string(image, lang="chi_sim+eng")
        except Exception as exc:
            # 语言包缺失时退回默认语言，再次失败才算引擎不可用。
            try:
                return pytesseract.image_to_string(image)
            except Exception:
                raise OcrUnavailableError(f"tesseract 识别失败：{exc}") from exc


# ---------------------------------------------------------------------------
# 引擎链
# ---------------------------------------------------------------------------


def _coerce_engine(candidate) -> OcrEngine:
    engine = candidate() if isinstance(candidate, type) else candidate
    if not callable(getattr(engine, "recognize", None)):
        raise TypeError(
            "ocr_engine 必须提供 recognize(png_bytes: bytes) -> str；"
            f"收到 {type(candidate)!r}"
        )
    return engine


def build_default_engines() -> list[OcrEngine]:
    """按优先级构建本机可用的引擎列表（不做真实识别）。"""
    engines: list[OcrEngine] = []
    for factory in (WindowsOcrEngine, RapidOcrEngine, TesseractEngine):
        try:
            if factory.available():
                engines.append(factory())
        except Exception as exc:  # 探测本身失败不应炸掉整条链
            logger.debug("OCR 引擎探测失败 %s: %s", factory.__name__, exc)
    return engines


def resolve_engines(ocr_engine=None) -> list[OcrEngine]:
    """解析 ``ocr_engine=`` 注入：``None`` → 本机默认链；对象/列表 → 原样使用。"""
    if ocr_engine is None:
        return build_default_engines()
    if isinstance(ocr_engine, (list, tuple, set)):
        return [_coerce_engine(item) for item in ocr_engine]
    return [_coerce_engine(ocr_engine)]


def run_ocr_chain(
    png_bytes: bytes,
    engines: Sequence[OcrEngine] | None = None,
    *,
    context: str = "",
    timeout: float | None = None,
) -> OcrRunResult:
    """按顺序尝试引擎，返回第一个识别出文本的结果。

    - 单个引擎「不可用/识别失败」只记 warning，继续下一个；
    - 所有引擎都抛错 → :class:`OcrUnavailableError`；
    - 引擎能运行但确实没识别到字 → 返回 ``text=""``（附 warning），由调用方
      决定是记 warning 还是报错（例如空白扫描页）；
    - ``timeout`` 是整条链的时限（秒），超时即放弃后续引擎并抛
      :class:`OcrUnavailableError`，防止异常文件把请求拖死。
    """
    prefix = f"{context}：" if context else ""
    engine_list = list(engines) if engines is not None else build_default_engines()
    warnings: list[str] = []
    if not engine_list:
        raise OcrUnavailableError(
            f"{prefix}没有可用的 OCR 引擎（Windows OCR / rapidocr / tesseract 均不可用）"
        )
    deadline = time.monotonic() + float(timeout) if timeout and timeout > 0 else None
    attempted = False
    for engine in engine_list:
        name = str(getattr(engine, "name", type(engine).__name__))
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                warnings.append(f"{prefix}OCR 整体超时（{timeout:g}s），已放弃后续引擎")
                break
            # 把剩余时间收紧到引擎自己的子进程超时上（若它支持）。
            if hasattr(engine, "timeout"):
                try:
                    engine.timeout = min(float(engine.timeout), max(1.0, remaining))
                except (TypeError, ValueError):
                    pass
        try:
            raw = engine.recognize(png_bytes)
        except OcrUnavailableError as exc:
            warnings.append(f"{prefix}OCR 引擎 {name} 不可用：{exc}")
            continue
        except Exception as exc:
            warnings.append(
                f"{prefix}OCR 引擎 {name} 识别失败：{type(exc).__name__}: {exc}"
            )
            continue
        attempted = True
        text = normalize_ocr_text(raw if isinstance(raw, str) else str(raw or ""))
        if text.strip():
            return OcrRunResult(text=text, engine=name, warnings=warnings)
        warnings.append(f"{prefix}OCR 引擎 {name} 未识别到文本")
    if not attempted:
        detail = "；".join(warnings) or f"{prefix}OCR 引擎均不可用"
        raise OcrUnavailableError(f"{prefix}所有 OCR 引擎均不可用：{detail}")
    return OcrRunResult(text="", engine=None, warnings=warnings)
