"""Analytics Agent 对照 Web 入口：保留 Stage 1/2，但不接入记忆。

与 ``web_app.py`` 的差异只有记忆层：

- 使用空的 ``DemoAgentMemory``；
- 使用 ``DefaultLlmContextEnhancer(None)``，不注入任何记忆；
- 不挂载 /memory 和 /api/memory。

这样两个页面使用同一套 schema/glossary prompt、7 个工具和用户解析，
只改变“有无记忆”一个变量，便于人工对比。

运行：
    python -m analytics_agent.web_app_without_memory

默认端口 8001，避免和有记忆的 8000 冲突；可用
``WEB_WITHOUT_MEMORY_PORT`` 覆盖。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

# VSCode 直接运行脚本时，sys.path[0] 是脚本目录；补上仓库根目录。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from vanna import Agent
from vanna.core.enhancer import DefaultLlmContextEnhancer
from vanna.integrations.local.agent_memory import DemoAgentMemory
from vanna.servers.base import ChatHandler
from vanna.servers.fastapi.routes import register_chat_routes

from analytics_agent.agent_factory import build_agent
from analytics_agent.config import Settings
from analytics_agent.web_app import (
    BarFirstChartGenerator,
    WebUserResolver,
    load_index_html,
)


def create_agent(settings: Settings) -> Agent:
    """同 Stage 1/2 Agent，但显式关闭记忆注入。"""
    return build_agent(
        settings,
        agent_memory=DemoAgentMemory(max_items=1000),
        llm_context_enhancer=DefaultLlmContextEnhancer(None),
        stream_responses=True,
        user_resolver=WebUserResolver(),
        plotly_generator=BarFirstChartGenerator(),
    )


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    """构建无记忆对照 Web 应用。"""
    s = settings or Settings.from_env()
    app = FastAPI(title="Analytics Agent (no memory)")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return load_index_html()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "memory": "disabled"}

    register_chat_routes(app, ChatHandler(create_agent(s)), {})
    return app


def main() -> None:
    settings = Settings.from_env()
    host = os.getenv("WEB_HOST", "0.0.0.0").strip() or "0.0.0.0"
    port = int(os.getenv("WEB_WITHOUT_MEMORY_PORT", "8001"))
    print("=" * 64)
    print(f"LLM        : {settings.llm_provider} / {settings.llm_model}")
    print("记忆       : 关闭（DemoAgentMemory + enhancer=None）")
    print("工具       : Stage 1/2 的 7 个工具（schema/glossary/护栏）")
    print(f"浏览器打开 : http://localhost:{port}")
    print("=" * 64)
    uvicorn.run(create_app(settings), host=host, port=port)


if __name__ == "__main__":
    main()
