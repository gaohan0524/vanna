"""
FastAPI route implementations for Vanna Agents.

本模块是 HTTP 层与 Agent 之间的接线层，承载整条 Text2SQL 链路的入口：
它把 FastAPI 的原始请求对象转换为框架中立的数据结构（RequestContext / ChatRequest），
再委托 ChatHandler 驱动 Agent 产出 UiComponent，最终编码成 SSE / WebSocket / 轮询
三种传输格式返回给前端。核心端点 chat_sse 是生产环境最常用的流式通道。
"""

import asyncio
import json
import traceback
from typing import Any, AsyncGenerator, Dict, Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse, HTMLResponse

from ..base import ChatHandler, ChatRequest, ChatResponse
from ..base.templates import get_index_html
from ...core.user.request_context import RequestContext


async def _await_task(task: "asyncio.Task") -> None:
    """Await a task while swallowing cancellation/errors (defensive cleanup).

    用于清理后台任务：流式任务在取消路径下会抛出 asyncio.CancelledError，正常路径下
    的异常也已在任务内部自行兜底（回发 error 帧）。这里统一吞掉，避免二次抛出打断
    主循环。
    """
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


def register_chat_routes(
    app: FastAPI, chat_handler: ChatHandler, config: Optional[Dict[str, Any]] = None
) -> None:
    """Register chat routes on FastAPI app.

    Args:
        app: FastAPI application
        chat_handler: Chat handler instance
        config: Server configuration

    在 FastAPI 应用上注册首页、SSE、WebSocket、轮询四类路由。所有端点共享同一个
    chat_handler，而 chat_handler 内部又共享同一个 Agent 实例，因此整条链路的
    状态（会话、记忆、工具注册）是天然单例的。
    """
    config = config or {}

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        """Serve the main chat interface.

        返回聊天首页 HTML。dev_mode 下从本地静态目录加载前端组件，否则从 CDN 加载
        （默认 https://img.vanna.ai/vanna-components.js）。
        """
        dev_mode = config.get("dev_mode", False)
        cdn_url = config.get("cdn_url", "https://img.vanna.ai/vanna-components.js")
        api_base_url = config.get("api_base_url", "")

        return get_index_html(
            dev_mode=dev_mode, cdn_url=cdn_url, api_base_url=api_base_url
        )

    @app.post("/api/vanna/v2/chat_sse")
    async def chat_sse(
        chat_request: ChatRequest, http_request: Request
    ) -> StreamingResponse:
        """Server-Sent Events endpoint for streaming chat.

        流式聊天的核心端点（POST + SSE）。流程：从 FastAPI 原始请求中抽取 cookie /
        header / IP / query 组成 RequestContext（供 UserResolver 解析身份）→ 交给
        ChatHandler.handle_stream → Agent 产出 UiComponent → 逐块编码为
        `data: {json}\n\n`，最后以 `data: [DONE]\n\n` 结束。

        取消（对应 Vercel AI SDK 的 abort / 关闭 ReadableStream、Claude Code 关闭
        SSE 连接）：SSE 为单向通道，客户端无法在流中发取消帧，取消的唯一信号就是
        断开 TCP 连接。后台 watcher 周期探测 http_request.is_disconnected()，一旦断开
        即置位 cancel_event，Agent 据此中止在途的 LLM 流式调用并抛出
        asyncio.CancelledError 向上传播，从而停止 token 计费。
        """
        # Extract request context for user resolution
        # 关键：把 FastAPI 的原始请求信息规范化成框架中立的 RequestContext。
        # UserResolver 只认识这个结构，从而与具体 Web 框架解耦。
        chat_request.request_context = RequestContext(
            cookies=dict(http_request.cookies),
            headers=dict(http_request.headers),
            remote_addr=http_request.client.host if http_request.client else None,
            query_params=dict(http_request.query_params),
            metadata=chat_request.metadata,
        )

        # 取消信号：客户端断开连接时由 watcher 置位。
        cancel_event = asyncio.Event()

        async def generate() -> AsyncGenerator[str, None]:
            """Generate SSE stream.

            SSE 生成器：每收到一个 ChatStreamChunk 就编码一行 `data: {json}`；
            正常结束补发 `data: [DONE]`；异常则回传 error 数据，避免连接静默断开。
            客户端断开时 watcher 置位 cancel_event，Agent 抛 asyncio.CancelledError，
            此处捕获后重新抛出（连接已断，无需回写任何帧）。
            """

            async def watch_disconnect() -> None:
                # 周期性探测客户端是否已断开（Starlette Request.is_disconnected）。
                # 一旦断开即置位取消信号，触发 Agent 中止 LLM 流式调用。
                while not cancel_event.is_set():
                    if await http_request.is_disconnected():
                        cancel_event.set()
                        break
                    await asyncio.sleep(0.1)

            watcher = asyncio.create_task(watch_disconnect())
            try:
                try:
                    async for chunk in chat_handler.handle_stream(
                        chat_request, cancel_event=cancel_event
                    ):
                        chunk_json = chunk.model_dump_json()
                        yield f"data: {chunk_json}\n\n"
                    yield "data: [DONE]\n\n"
                except asyncio.CancelledError:
                    # 客户端断开：连接已不在，直接向上抛取消，由 ASGI 服务器收尾。
                    raise
                except Exception as e:
                    traceback.print_stack()
                    traceback.print_exc()
                    # 错误以带 type=error 的数据帧回传，前端据此提示而非无响应
                    error_data = {
                        "type": "error",
                        "data": {"message": str(e)},
                        "conversation_id": chat_request.conversation_id or "",
                        "request_id": chat_request.request_id or "",
                    }
                    yield f"data: {json.dumps(error_data)}\n\n"
            finally:
                watcher.cancel()

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",   # SSE 必须禁用缓存，否则会被代理/浏览器缓存
                "Connection": "keep-alive",     # 保持长连接持续推送
                "X-Accel-Buffering": "no",  # Disable nginx buffering —— 禁用 nginx 缓冲，保证实时性
            },
        )

    @app.websocket("/api/vanna/v2/chat_websocket")
    async def chat_websocket(websocket: WebSocket) -> None:
        """WebSocket endpoint for real-time chat.

        双向实时通道：与 chat_sse 逻辑等价，只是用 WebSocket 消息承载分片。
        额外支持显式取消：流式进行中客户端可发送 {"type": "cancel"}，或直接断开连接，
        都会中止在途的 LLM 调用并回发 cancelled 帧。
        （注意：flask 版本不支持，仅 fastapi 提供。）
        """
        await websocket.accept()

        try:
            while True:
                # Receive message
                try:
                    data = await websocket.receive_json()
                except WebSocketDisconnect:
                    break

                # Extract request context for user resolution
                try:
                    metadata = data.get("metadata", {})
                    data["request_context"] = RequestContext(
                        cookies=dict(websocket.cookies),
                        headers=dict(websocket.headers),
                        remote_addr=websocket.client.host if websocket.client else None,
                        query_params=dict(websocket.query_params),
                        metadata=metadata,
                    )

                    chat_request = ChatRequest(**data)
                except Exception as e:
                    traceback.print_stack()
                    traceback.print_exc()
                    await websocket.send_json(
                        {
                            "type": "error",
                            "data": {"message": f"Invalid request: {str(e)}"},
                        }
                    )
                    continue

                cancel_event = asyncio.Event()

                async def stream_and_send() -> None:
                    try:
                        async for chunk in chat_handler.handle_stream(
                            chat_request, cancel_event=cancel_event
                        ):
                            await websocket.send_json(chunk.model_dump())

                        # Send completion signal
                        await websocket.send_json(
                            {
                                "type": "completion",
                                "data": {"status": "done"},
                                "conversation_id": chat_request.conversation_id or "",
                                "request_id": chat_request.request_id or "",
                            }
                        )
                    except asyncio.CancelledError:
                        # 显式 cancel 或客户端断开导致的取消：回发 cancelled 帧后结束
                        await websocket.send_json(
                            {
                                "type": "cancelled",
                                "data": {"status": "cancelled"},
                                "conversation_id": chat_request.conversation_id or "",
                                "request_id": chat_request.request_id or "",
                            }
                        )
                        raise
                    except Exception as e:
                        traceback.print_stack()
                        traceback.print_exc()
                        await websocket.send_json(
                            {
                                "type": "error",
                                "data": {"message": str(e)},
                                "conversation_id": chat_request.conversation_id or "",
                                "request_id": chat_request.request_id or "",
                            }
                        )

                stream_task = asyncio.create_task(stream_and_send())

                # 流式进行期间，同时监听「流结束」与「下一条消息（cancel / 断开）」，
                # 谁先完成先处理谁。
                while not stream_task.done():
                    recv_task = asyncio.create_task(websocket.receive_json())
                    done, pending = await asyncio.wait(
                        {stream_task, recv_task}, return_when=asyncio.FIRST_COMPLETED
                    )

                    if stream_task in done:
                        # 流先结束：丢弃多余的接收任务（客户端可重发下一条）
                        recv_task.cancel()
                        await _await_task(recv_task)
                        break

                    # 流未结束，但收到了一条消息（可能是 cancel）
                    cancel_requested = False
                    try:
                        control = recv_task.result()
                        cancel_requested = (
                            isinstance(control, dict)
                            and control.get("type") == "cancel"
                        )
                    except Exception:
                        # 接收异常（含 WebSocketDisconnect）也视为需要中止
                        cancel_requested = True

                    if cancel_requested:
                        cancel_event.set()
                        await _await_task(stream_task)
                        break

                await _await_task(stream_task)

        except WebSocketDisconnect:
            pass
        except Exception as e:
            traceback.print_stack()
            traceback.print_exc()
            try:
                await websocket.send_json(
                    {
                        "type": "error",
                        "data": {"message": f"WebSocket error: {str(e)}"},
                    }
                )
            except Exception:
                pass
            finally:
                await websocket.close()

    @app.post("/api/vanna/v2/chat_poll")
    async def chat_poll(
        chat_request: ChatRequest, http_request: Request
    ) -> ChatResponse:
        """Polling endpoint for chat.

        轮询端点：一次性跑完整个流并聚合成完整 ChatResponse 返回，适合不支持 SSE
        的客户端。逻辑上等价于 handle_stream 的同步聚合。
        """
        # Extract request context for user resolution
        chat_request.request_context = RequestContext(
            cookies=dict(http_request.cookies),
            headers=dict(http_request.headers),
            remote_addr=http_request.client.host if http_request.client else None,
            query_params=dict(http_request.query_params),
            metadata=chat_request.metadata,
        )

        try:
            result = await chat_handler.handle_poll(chat_request)
            return result
        except Exception as e:
            traceback.print_stack()
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"Chat failed: {str(e)}")
