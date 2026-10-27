"""清理过期的工作目录（隐私保留期）。

``workspace/runs`` 下的报告、解析文本、OCR 中间产物会随着使用不断堆积，真实简历的
PDF 与解析文本都属于 PII。这个脚本按「最后修改时间」清理超过保留期的文件/目录，
默认只做 dry-run 打印，确认后加 ``--yes`` 才真正删除。

安全边界：
- 只允许清理 ``workspace`` 之内的路径（防止把仓库或用户目录误删）；
- 默认只清 ``workspace/runs``，test_resumes 需要显式 ``--include-test-resumes``；
- 删除前逐个打印路径与大小，方便先核对。

用法::

    python scripts/purge-runs.py --older-than-days 30            # 看会删什么
    python scripts/purge-runs.py --older-than-days 30 --yes      # 真删
    python scripts/purge-runs.py --include-test-resumes --older-than-days 7 --yes
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = (PROJECT_ROOT / "workspace").resolve()
DEFAULT_RUNS_ROOT = WORKSPACE_ROOT / "runs"
DEFAULT_RESUMES_ROOT = WORKSPACE_ROOT / "test_resumes"


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return False
    return True


def _size_text(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} B"


def _entry_size(path: Path) -> int:
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            try:
                total += child.stat().st_size
            except OSError:
                pass
    return total


def _collect_expired(root: Path, cutoff: float, *, skip: set[Path]) -> list[Path]:
    """Return top-most expired entries under ``root`` (deleting a directory covers its children)."""
    expired: list[Path] = []
    if not root.exists():
        return expired
    for entry in sorted(root.iterdir()):
        resolved = entry.resolve()
        if resolved in skip:
            continue
        try:
            newest = entry.stat().st_mtime
        except OSError:
            continue
        if entry.is_dir():
            for child in entry.rglob("*"):
                try:
                    newest = max(newest, child.stat().st_mtime)
                except OSError:
                    continue
        if newest < cutoff:
            expired.append(entry)
    return expired


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按保留期清理 workspace/runs 下的过期产物（默认 dry-run）")
    parser.add_argument("--root", default=str(DEFAULT_RUNS_ROOT), help="要清理的目录（必须在 workspace 内）")
    parser.add_argument("--older-than-days", type=int, default=30, help="保留下最近 N 天修改过的内容（默认 30）")
    parser.add_argument(
        "--include-test-resumes",
        action="store_true",
        help="同时清理 workspace/test_resumes 下过期的简历副本（PII，显式开启）",
    )
    parser.add_argument("--yes", action="store_true", help="真正删除；不加只打印将要删除的内容")
    args = parser.parse_args(argv)

    if args.older_than_days < 0:
        print("--older-than-days 不能为负数", file=sys.stderr)
        return 2

    targets: list[Path] = []
    root = Path(args.root).expanduser()
    if not root.is_absolute():
        root = (PROJECT_ROOT / root).resolve()
    if not _within(root, WORKSPACE_ROOT):
        print(f"拒绝清理：{root} 不在 workspace（{WORKSPACE_ROOT}）之内", file=sys.stderr)
        return 2
    cutoff = time.time() - args.older_than_days * 86400
    print(f"保留期：{args.older_than_days} 天；截止时间：{time.strftime('%Y-%m-%d %H:%M', time.localtime(cutoff))}")
    print(f"扫描：{root}")
    targets.extend(_collect_expired(root, cutoff, skip=set()))
    if args.include_test_resumes:
        print(f"扫描：{DEFAULT_RESUMES_ROOT}（含 PII，仅显式清理）")
        targets.extend(_collect_expired(DEFAULT_RESUMES_ROOT, cutoff, skip={root}))

    total = 0
    for path in targets:
        size = _entry_size(path)
        total += size
        print(f"{'[删除]' if args.yes else '[dry-run]'} {path.relative_to(PROJECT_ROOT)}（{_size_text(size)}）")
        if args.yes:
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            except OSError as exc:
                print(f"  删除失败：{exc}", file=sys.stderr)
    print(f"共 {len(targets)} 项，合计 {_size_text(total)}" + ("" if args.yes else "；加 --yes 才会真正删除"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
