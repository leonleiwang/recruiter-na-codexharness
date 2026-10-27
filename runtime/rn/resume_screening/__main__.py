"""允许 ``python -m rn.resume_screening`` 直接调用命令行入口。"""
from __future__ import annotations

from rn.resume_screening.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
