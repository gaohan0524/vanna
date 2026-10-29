"""兼容入口：正式交付 Web 入口已迁移到 ``analytics_agent.web_app``。

保留这个文件是为了不破坏旧的运行命令：
    python src/vanna/examples/web_manual_test.py
    python -m vanna.examples.web_manual_test

新代码请使用：
    python -m analytics_agent.web_app
"""

from __future__ import annotations

import sys
from pathlib import Path

# 直接以脚本方式运行时，确保仓库根目录在 sys.path 中。
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from analytics_agent.web_app import create_app, main  # noqa: E402,F401

if __name__ == "__main__":
    main()
