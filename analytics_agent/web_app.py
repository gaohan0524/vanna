"""Analytics Agent 交付用 Web 入口。

用途：
- 用浏览器直接和“带 Stage 1/2 能力”的正式 Agent 对话；
- 复用 ``analytics_agent.agent_factory.build_agent``，不在 Web 层重复装配 LLM、
  ClickHouse、工具、schema/glossary prompt；
- 保留自定义 Plotly 首页和 admin/analyst 用户解析。

运行：
    python -m analytics_agent.web_app

配置：
    所有连接/模型参数仍从根目录 ``.env`` 读取，和评测 CLI 完全一致。
    WEB_HOST / WEB_PORT / WEB_DEFAULT_EMAIL / WEB_ADMIN_EMAILS 仅控制 Web 层。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# VSCode 的“运行 Python 文件”会把脚本所在目录放到 sys.path[0]，
# 此时直接 import analytics_agent 会失败。把仓库根目录补进来，
# 让“右键运行”和 `python -m analytics_agent.web_app` 都能工作。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from vanna import Agent, User
from vanna.core.tool import ToolContext
from vanna.core.user import RequestContext, UserResolver
from vanna.integrations.plotly import PlotlyChartGenerator
from vanna.servers.base import ChatHandler
from vanna.servers.fastapi.routes import register_chat_routes

from analytics_agent.agent_factory import build_agent, build_memory
from analytics_agent.config import Settings


def _default_web_email() -> str:
    return os.getenv("WEB_DEFAULT_EMAIL", "analyst@example.com").strip()


def _admin_emails() -> set[str]:
    """显式配置的管理员邮箱；不把“无 cookie”默认成 admin。"""
    raw = os.getenv("WEB_ADMIN_EMAILS", "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


_WEB_INDEX_PATH = Path(__file__).parent / "web" / "index.html"
_WEB_MEMORY_PATH = Path(__file__).parent / "web" / "memory.html"


def load_index_html() -> str:
    """读取业务侧首页模板；模板不放进 vanna 框架默认页面。"""
    return _WEB_INDEX_PATH.read_text(encoding="utf-8")


def load_memory_html() -> str:
    """读取记忆查看页模板。"""
    return _WEB_MEMORY_PATH.read_text(encoding="utf-8")


class BarFirstChartGenerator(PlotlyChartGenerator):
    """列数较多时也优先输出柱状图（仅本测试脚本使用）。

    框架默认实现（src/vanna/integrations/plotly/chart_generator.py）在
    ``len(df.columns) >= 4`` 时直接退化成 Plotly table —— 于是「渠道 + 订单数 +
    GMV + 客单价 + 占比」这类结果只会渲染成表格，看不到柱子。这里先裁出
    「类别列 + 主数值列（求和最大者）」两列，再交给默认实现画成柱状图；
    其余情况（列数 < 4、没有类别列等）仍走默认逻辑。
    """

    def generate_chart(self, df: pd.DataFrame, title: str = "Chart") -> Dict[str, Any]:
        cat_cols = df.select_dtypes(include=["object", "category"]).columns.tolist()
        num_cols = df.select_dtypes(include=["number"]).columns.tolist()

        if len(df.columns) >= 4 and cat_cols and num_cols:
            # 多指标并存时取求和最大的数值列作为主指标（通常是金额 / GMV 类）
            main_metric = max(num_cols, key=lambda col: float(df[col].fillna(0).sum()))
            return super().generate_chart(df[[cat_cols[0], main_metric]], title)

        return super().generate_chart(df, title)


class WebUserResolver(UserResolver):
    """从 cookie 读取邮箱并分配 admin/analyst 分组。

    默认 analyst，避免“无 cookie 即 admin”；管理员需要通过
    ``WEB_ADMIN_EMAILS`` 或 ``admin@`` 邮箱显式指定。
    """

    async def resolve_user(self, request_context: RequestContext) -> User:
        email = (
            request_context.get_cookie("vanna_email")
            or _default_web_email()
            or "analyst@example.com"
        ).strip()
        normalized = email.lower()
        is_admin = normalized in _admin_emails() or normalized.startswith("admin@")
        group = "admin" if is_admin else "analyst"
        return User(id=email, email=email, group_memberships=[group])


def create_agent(settings: Settings, memory=None) -> Agent:
    """构建交付用 Agent：Stage 1/2 的 prompt、7 个工具和护栏都启用。"""

    return build_agent(
        settings,
        agent_memory=memory,
        stream_responses=True,
        user_resolver=WebUserResolver(),
        plotly_generator=BarFirstChartGenerator(),
    )


async def _memory_payload(request: Request, memory, backend: str) -> Dict[str, Any]:
    """返回当前 cookie 用户可见的、可读的文本/工具记忆。

    不返回 embedding 向量：向量是数字数组，对人工排查没有意义；
    Chroma 中的 document 和 metadata 才是可读事实。
    """
    resolver = WebUserResolver()
    request_context = RequestContext(
        cookies=dict(request.cookies),
        headers=dict(request.headers),
    )
    user = await resolver.resolve_user(request_context)
    tool_context = ToolContext(
        user=user,
        conversation_id="memory_viewer",
        request_id="memory_viewer",
        agent_memory=memory,
    )

    list_fn = getattr(memory, "list_memories", None)
    items = await list_fn(tool_context, limit=500) if list_fn else []
    counts = {
        "text": sum(1 for item in items if item.get("kind") == "text"),
        "tool": sum(1 for item in items if item.get("kind") == "tool"),
    }
    return {
        "backend": backend,
        "user": {
            "id": user.id,
            "groups": user.group_memberships,
        },
        "counts": counts,
        "total": len(items),
        "items": items,
    }


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    """构建 FastAPI 应用。

    先注册自定义首页覆盖框架默认页：Starlette 按注册顺序匹配，先注册的 "/"
    会命中，后注册的默认极简页即成为死路由。
    """

    s = settings or Settings.from_env()
    memory = build_memory(s)
    app = FastAPI(title="Analytics Agent")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return load_index_html()

    @app.get("/memory", response_class=HTMLResponse)
    async def memory_page() -> str:
        return load_memory_html()

    @app.get("/api/memory")
    async def memory_api(request: Request) -> Dict[str, Any]:
        return await _memory_payload(request, memory, s.memory_backend)

    register_chat_routes(app, ChatHandler(create_agent(s, memory)), {})

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def main() -> None:
    # Settings.from_env 会加载 .env；之后再读 WEB_*，避免读到未加载的值。
    settings = Settings.from_env()
    host = os.getenv("WEB_HOST", "0.0.0.0").strip() or "0.0.0.0"
    port = int(os.getenv("WEB_PORT", "8000"))
    print("=" * 64)
    print(f"LLM        : {settings.llm_provider} / {settings.llm_model}")
    print(
        f"ClickHouse : {settings.ch_user}@{settings.ch_host}:"
        f"{settings.ch_port}/{settings.ch_database}"
    )
    print("工具       : Stage 1/2 的 7 个工具（schema/glossary/护栏）")
    print(f"工具上限   : 单轮最多 {settings.agent_max_iterations} 次工具调用")
    print("图表       : 已启用 Plotly 渲染（cdn.plot.ly）")
    print(f"浏览器打开 : http://localhost:{port}")
    print("=" * 64)
    uvicorn.run(create_app(settings), host=host, port=port)


if __name__ == "__main__":
    main()
