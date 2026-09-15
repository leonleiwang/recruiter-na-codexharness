#!/usr/bin/env python3
"""Fail CI if obvious credentials are embedded in executable source files."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = [ROOT / "runtime", ROOT / "im", ROOT / "scripts", ROOT / "deploy"]
SOURCE_SUFFIXES = {".py", ".js", ".mjs", ".ts", ".sh", ".service"}
SKIP_NAMES = {Path(__file__).name}
PLACEHOLDER_TOKENS = {
    "", "changeme", "change-me", "your_key", "your-key", "your_secret", "your-secret",
    "example", "placeholder", "<secret>", "${secret}", "${api_key}", "${password}",
}
KNOWN_FORBIDDEN = ["dazd@20161019"]
ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|api[_-]?key|access[_-]?token|client[_-]?secret)\b\s*[:=]\s*([\"'])([^\"'\n]{6,})\2"
)


def is_placeholder(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in PLACEHOLDER_TOKENS:
        return True
    return any(token in lowered for token in ("your_", "your-", "placeholder", "example", "changeme"))


def main() -> int:
    findings: list[str] = []
    for base in SCAN_ROOTS:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.name in SKIP_NAMES or path.suffix not in SOURCE_SUFFIXES:
                continue
            if any(part in {".venv", "node_modules", "__pycache__"} for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for secret in KNOWN_FORBIDDEN:
                if secret in text:
                    findings.append(f"{path.relative_to(ROOT)}: known embedded credential")
            for match in ASSIGNMENT.finditer(text):
                value = match.group(3)
                if not is_placeholder(value):
                    line = text.count("\n", 0, match.start()) + 1
                    findings.append(f"{path.relative_to(ROOT)}:{line}: literal credential-like assignment")
    if findings:
        print("SECRET_SCAN_FAILED", file=sys.stderr)
        for item in sorted(set(findings)):
            print(item, file=sys.stderr)
        return 4
    print("SECRET_SCAN_CLEAN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
