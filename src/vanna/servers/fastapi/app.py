"""
FastAPI server factory for Vanna Agents.

本模块提供 FastAPI 服务器工厂 VannaFastAPIServer：把 Agent 包装成可运行的 Web 服务，
负责组装 CORS、静态文件、聊天路由与健康检查，并处理 Jupyter/Colab 等异步环境的
启动差异。它是把「纯框架层 Agent」变成「可对外服务的 API」的最后一公里。
"""

from typing import Any, Dict, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from ...core import Agent
from ..base import ChatHandler
from .routes import register_chat_routes


class VannaFastAPIServer:
    """FastAPI server factory for Vanna Agents.

    服务器工厂：持有一个 Agent（及其 ChatHandler），create_app 返回配置好的 FastAPI
    应用，run 则负责实际启动（并适配 Jupyter/Colab 的已有事件循环）。
    """

    def __init__(self, agent: Agent, config: Optional[Dict[str, Any]] = None):
        """Initialize FastAPI server.

        Args:
            agent: The agent to serve (must have user_resolver configured)
            config: Optional server configuration
        """
        self.agent = agent
        self.config = config or {}
        # 若提供 storage 配置段，则按配置替换 Agent 的会话存储与记忆后端
        # （进程内原地替换，随后创建的 ChatHandler 自然使用新后端）
        storage_config = self.config.get("storage")
        if storage_config:
            from ...config import apply_storage_config

            apply_storage_config(agent, storage_config)
        # 可观测性配置（OTel / Langfuse / LangSmith / composite），原地注入 provider
        observability_config = self.config.get("observability")
        if observability_config:
            from ...config import apply_observability_config

            apply_observability_config(agent, observability_config)
        # 每个 server 实例一个 ChatHandler；其内部复用同一个 Agent
        self.chat_handler = ChatHandler(agent)

    def create_app(self) -> FastAPI:
        """Create configured FastAPI app.

        Returns:
            Configured FastAPI application
        """
        # Create FastAPI app
        app_config = self.config.get("fastapi", {})
        app = FastAPI(
            title="Vanna Agents API",
            description="API server for Vanna Agents framework",
            version="0.1.0",
            **app_config,
        )

        # Configure CORS if enabled
        # CORS 默认开启并允许所有来源（开发期便利；生产应按需收紧 allow_origins）
        cors_config = self.config.get("cors", {})
        if cors_config.get("enabled", True):
            cors_params = {k: v for k, v in cors_config.items() if k != "enabled"}

            # Set sensible defaults
            cors_params.setdefault("allow_origins", ["*"])
            cors_params.setdefault("allow_credentials", True)
            cors_params.setdefault("allow_methods", ["*"])
            cors_params.setdefault("allow_headers", ["*"])

            app.add_middleware(CORSMiddleware, **cors_params)

        # Add static file serving in dev mode
        # dev 模式下从本地 static 目录挂载前端组件，便于本地调试而不依赖 CDN
        dev_mode = self.config.get("dev_mode", False)
        if dev_mode:
            static_folder = self.config.get("static_folder", "static")
            try:
                import os

                if os.path.exists(static_folder):
                    app.mount(
                        "/static", StaticFiles(directory=static_folder), name="static"
                    )
            except Exception:
                pass  # Static files not available

        # Register routes
        # 注册首页 / SSE / WebSocket / 轮询 四类聊天路由
        register_chat_routes(app, self.chat_handler, self.config)

        # Add health check
        @app.get("/health")
        async def health_check() -> Dict[str, str]:
            return {"status": "healthy", "service": "vanna"}

        return app

    def run(self, **kwargs: Any) -> None:
        """Run the FastAPI server.

        This method automatically detects if running in an async environment
        (Jupyter, Colab, IPython, etc.) and:
        - Uses appropriate async handling for existing event loops
        - Sets up port forwarding if in Google Colab
        - Displays the correct URL for accessing the app

        Args:
            **kwargs: Arguments passed to uvicorn configuration

        启动入口：先探测是否处于 Jupyter/Colab 等「已有事件循环」环境——若是，先
        nest_asyncio 打补丁再用 asyncio.run 驱动 uvicorn；否则走标准 uvicorn.run。
        并在 Colab 下做端口转发、打印可访问地址。
        """
        import sys
        import asyncio
        import uvicorn

        # Check if we're in an environment with a running event loop FIRST
        in_async_env = False
        try:
            asyncio.get_running_loop()
            in_async_env = True
        except RuntimeError:
            in_async_env = False

        # If in async environment, apply nest_asyncio BEFORE creating the app
        # 在创建 app 前就应用 nest_asyncio，否则 Jupyter 里无法在已有事件循环中跑 uvicorn
        if in_async_env:
            try:
                import nest_asyncio

                nest_asyncio.apply()
            except ImportError:
                print("Warning: nest_asyncio not installed. Installing...")
                import subprocess

                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", "nest_asyncio"]
                )
                import nest_asyncio

                nest_asyncio.apply()

        # Now create the app after nest_asyncio is applied
        app = self.create_app()

        # Set defaults
        run_kwargs = {"host": "0.0.0.0", "port": 8000, "log_level": "info", **kwargs}

        # Get the port and other config from run_kwargs
        port = run_kwargs.get("port", 8000)
        host = run_kwargs.get("host", "0.0.0.0")
        log_level = run_kwargs.get("log_level", "info")

        # Check if we're specifically in Google Colab for port forwarding
        in_colab = "google.colab" in sys.modules

        if in_colab:
            try:
                from google.colab import output

                output.serve_kernel_port_as_window(port)
                from google.colab.output import eval_js

                print("Your app is running at:")
                print(eval_js(f"google.colab.kernel.proxyPort({port})"))
            except Exception as e:
                print(f"Warning: Could not set up Colab port forwarding: {e}")
                print(f"Your app is running at: http://localhost:{port}")
        else:
            print("Your app is running at:")
            print(f"http://localhost:{port}")

        if in_async_env:
            # In Jupyter/Colab, create config with loop="asyncio" and use asyncio.run()
            # This matches the working pattern from Colab
            config = uvicorn.Config(
                app, host=host, port=port, log_level=log_level, loop="asyncio"
            )
            server = uvicorn.Server(config)
            asyncio.run(server.serve())
        else:
            # Normal execution outside of Jupyter/Colab
            uvicorn.run(app, **run_kwargs)
