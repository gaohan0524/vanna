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
from typing import Any, Dict, Optional

import pandas as pd
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from vanna import Agent, User
from vanna.core.user import RequestContext, UserResolver
from vanna.integrations.plotly import PlotlyChartGenerator
from vanna.servers.base import ChatHandler
from vanna.servers.fastapi.routes import register_chat_routes

from analytics_agent.agent_factory import build_agent
from analytics_agent.config import Settings


def _default_web_email() -> str:
    return os.getenv("WEB_DEFAULT_EMAIL", "analyst@example.com").strip()


def _admin_emails() -> set[str]:
    """显式配置的管理员邮箱；不把“无 cookie”默认成 admin。"""
    raw = os.getenv("WEB_ADMIN_EMAILS", "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


# 自定义首页：在框架极简页基础上补上 Plotly 图表渲染与表格样式。
# 富前端（<vanna-chat> Web Component）需要 CDN 或本地构建产物，这里直接用原生
# JS + Plotly CDN，离线时图表会降级为文字提示，不影响其他组件。
INDEX_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>智能体</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js" charset="utf-8"></script>
<style>
  :root { --navy:#023d60; --teal:#15a8a8; }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: #f5f7f9; color: #1f2933;
    font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
  }
  .wrap {
    max-width: 1000px; margin: 0 auto; padding: 16px;
    height: 100vh; display: flex; flex-direction: column;
  }
  header { padding: 4px 4px 12px; font-weight: 600; color: var(--navy); }
  header small { font-weight: 400; color: #6b7280; }
  #messages {
    flex: 1; overflow-y: auto; background: #fff; border: 1px solid #e5e7eb;
    border-radius: 12px; padding: 14px; display: flex; flex-direction: column; gap: 10px;
  }
  .row { font-size: 14px; line-height: 1.65; white-space: pre-wrap; word-break: break-word; }
  .row.user {
    align-self: flex-end; background: #e6f6f6; padding: 8px 12px;
    border-radius: 12px; max-width: 80%;
  }
  .row.ai { padding-left: 10px; border-left: 3px solid var(--teal); }
  .row.meta { color: #6b7280; font-size: 12px; }
  .sql {
    font-family: ui-monospace, Consolas, monospace; font-size: 12px;
    background: #0b1f2a; color: #d7f2f2; padding: 8px 10px; border-radius: 8px;
    margin-top: 6px; overflow-x: auto; white-space: pre;
  }
  table { border-collapse: collapse; font-size: 13px; margin-top: 4px; }
  th, td { border: 1px solid #e5e7eb; padding: 6px 10px; text-align: left; }
  th { background: #f0f7f8; color: var(--navy); }
  .chart-card {
    background: #fff; border: 1px solid #e5e7eb; border-radius: 10px;
    padding: 8px; margin-top: 4px;
  }
  .chart-title { font-size: 13px; color: var(--navy); margin: 4px 0 0 6px; }
  .chart { width: 100%; height: 420px; }
  form { display: flex; gap: 8px; margin-top: 12px; }
  textarea {
    flex: 1; resize: none; height: 58px; padding: 10px 12px; font: inherit;
    border: 1px solid #d1d5db; border-radius: 10px;
  }
  button { border: none; border-radius: 10px; padding: 0 18px; font: inherit; cursor: pointer; }
  #send { background: var(--teal); color: #fff; }
  #send:disabled { background: #9ca3af; cursor: not-allowed; }
  #stop { background: #fee2e2; color: #b91c1c; }
  #stop:disabled { opacity: .5; cursor: not-allowed; }
</style>
</head>
<body>
<div class="wrap">
  <header>智能体</header>
  <div id="messages"></div>
  <form id="form">
    <textarea id="input" placeholder="输入问题，Enter 发送，Shift+Enter 换行"></textarea>
    <button id="send" type="submit">发送</button>
    <button id="stop" type="button" disabled>停止</button>
  </form>
</div>
<script>
(function () {
  var messagesEl = document.getElementById('messages');
  var formEl = document.getElementById('form');
  var inputEl = document.getElementById('input');
  var stopBtn = document.getElementById('stop');
  var sendBtn = document.getElementById('send');

  var conversationId = null;
  var busy = false;
  var abortController = null;
  var currentAiTextId = null;
  var currentAiTextEl = null;
  var statusRow = null;
  var chartEls = {};

  function scrollBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function makeRow(cls) {
    var div = document.createElement('div');
    div.className = 'row ' + (cls || '');
    messagesEl.appendChild(div);
    scrollBottom();
    return div;
  }

  function setStatus(text) {
    if (!statusRow) {
      statusRow = makeRow('meta');
    }
    statusRow.textContent = text;
    scrollBottom();
  }

  // SQL 结果集：rows 为对象数组，columns 为列名数组
  function appendTable(rows, columns) {
    if (!rows || rows.length === 0) {
      makeRow('meta').textContent = '(空结果)';
      return;
    }
    var cols = (columns && columns.length) ? columns : Object.keys(rows[0]);
    var table = document.createElement('table');

    var thead = document.createElement('thead');
    var headRow = document.createElement('tr');
    for (var c = 0; c < cols.length; c++) {
      var th = document.createElement('th');
      th.textContent = cols[c];
      headRow.appendChild(th);
    }
    thead.appendChild(headRow);
    table.appendChild(thead);

    var tbody = document.createElement('tbody');
    for (var r = 0; r < rows.length; r++) {
      var tr = document.createElement('tr');
      for (var k = 0; k < cols.length; k++) {
        var td = document.createElement('td');
        var value = rows[r][cols[k]];
        td.textContent = (value === null || value === undefined) ? '' : String(value);
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    messagesEl.appendChild(table);
    scrollBottom();
  }

  // chart 组件：payload 形如 {data: [traces], layout: {...}, title: "...", ...}
  function appendChart(id, payload) {
    var traces = payload.data || [];
    var layout = payload.layout || {};
    var title = payload.title || '';
    // Plotly 自己会渲染 layout.title，标题相同就不再重复显示卡片标题
    var layoutTitle = '';
    if (layout.title) {
      layoutTitle = (typeof layout.title === 'string')
        ? layout.title
        : (layout.title.text || '');
    }

    var el = chartEls[id];
    if (!el) {
      var card = document.createElement('div');
      card.className = 'chart-card';
      if (title && title !== layoutTitle) {
        var titleEl = document.createElement('div');
        titleEl.className = 'chart-title';
        titleEl.textContent = title;
        card.appendChild(titleEl);
      }
      el = document.createElement('div');
      el.className = 'chart';
      card.appendChild(el);
      messagesEl.appendChild(card);
      chartEls[id] = el;
    }

    if (!window.Plotly) {
      el.textContent = 'Plotly 脚本未加载（需要能访问 cdn.plot.ly），图表数据已收到。';
      scrollBottom();
      return;
    }

    layout = Object.assign(
      { autosize: true, margin: { l: 60, r: 24, t: 40, b: 60 } },
      layout
    );
    window.Plotly.newPlot(el, traces, layout, {
      responsive: true,
      displayModeBar: false
    });
    scrollBottom();
  }

  // 工具参数（Text2SQL 中主要是 SQL）格式化
  function formatArgs(meta) {
    if (meta && typeof meta.sql === 'string') {
      return meta.sql;
    }
    try {
      return JSON.stringify(meta, null, 2);
    } catch (e) {
      return String(meta);
    }
  }

  // 处理单个流式分片：{rich: {id, type, lifecycle, data}, ...}
  function handleChunk(chunk) {
    if (!chunk) return;
    if (chunk.conversation_id) {
      conversationId = chunk.conversation_id;
    }

    var rich = chunk.rich || {};
    var data = rich.data || {};

    switch (rich.type) {
      case 'text':
        if (rich.lifecycle === 'create' || currentAiTextId !== rich.id) {
          currentAiTextId = rich.id;
          currentAiTextEl = makeRow('ai');
          currentAiTextEl.textContent = data.content || '';
        } else if (rich.lifecycle === 'update' && currentAiTextEl) {
          currentAiTextEl.textContent = data.content || '';
        }
        scrollBottom();
        break;
      case 'dataframe':
        appendTable(data.data || [], data.columns || []);
        break;
      case 'chart':
        appendChart(rich.id, data);
        break;
      case 'status_bar_update':
        if (data.message) setStatus(data.message);
        break;
      case 'status_card':
        var line = (data.title || '') +
          (data.description ? ' — ' + data.description : '');
        var row = makeRow('meta');
        row.textContent = line;
        if (data.metadata && Object.keys(data.metadata).length > 0) {
          var pre = document.createElement('div');
          pre.className = 'sql';
          pre.textContent = formatArgs(data.metadata);
          row.appendChild(pre);
          scrollBottom();
        }
        break;
      case 'notification':
        makeRow('meta').textContent = data.message || data.title || '';
        break;
      default:
        break;
    }
  }

  function setBusy(value) {
    busy = value;
    inputEl.disabled = value;
    sendBtn.disabled = value;
    stopBtn.disabled = !value;
  }

  async function send(message) {
    if (busy) return;
    setBusy(true);
    statusRow = null;

    var userRow = makeRow('user');
    userRow.textContent = message;

    var payload = { message: message };
    if (conversationId) {
      payload.conversation_id = conversationId;
    }

    abortController = new AbortController();
    try {
      var resp = await fetch('/api/vanna/v2/chat_sse', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: abortController.signal
      });

      if (!resp.ok || !resp.body) {
        makeRow('meta').textContent = '[错误] HTTP ' + resp.status;
        return;
      }

      var reader = resp.body.getReader();
      var decoder = new TextDecoder();
      var buffer = '';

      while (true) {
        var result = await reader.read();
        if (result.done) break;

        buffer += decoder.decode(result.value, { stream: true });

        // SSE 以空行分隔事件，逐个取出已完整的 "data: ..." 行
        var parts = buffer.split('\\n\\n');
        buffer = parts.pop();

        for (var i = 0; i < parts.length; i++) {
          var text = parts[i].trim();
          if (text.indexOf('data:') !== 0) continue;
          var raw = text.slice(5).trim();
          if (raw === '[DONE]') continue;
          try {
            handleChunk(JSON.parse(raw));
          } catch (e) {
            // 忽略无法解析的片段
          }
        }
      }
    } catch (err) {
      if (err && err.name === 'AbortError') {
        makeRow('meta').textContent = '[已停止] 生成已中断';
      } else {
        makeRow('meta').textContent = '[错误] ' + String(err);
      }
    } finally {
      abortController = null;
      setBusy(false);
      inputEl.focus();
    }
  }

  formEl.addEventListener('submit', function (e) {
    e.preventDefault();
    var text = inputEl.value.trim();
    if (!text) return;
    inputEl.value = '';
    send(text);
  });

  inputEl.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      formEl.requestSubmit();
    }
  });

  stopBtn.addEventListener('click', function () {
    if (abortController) {
      abortController.abort();
    }
  });

  inputEl.focus();
})();
</script>
</body>
</html>
"""


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


def create_agent(settings: Settings) -> Agent:
    """构建交付用 Agent：Stage 1/2 的 prompt、7 个工具和护栏都启用。"""

    return build_agent(
        settings,
        stream_responses=True,
        user_resolver=WebUserResolver(),
        plotly_generator=BarFirstChartGenerator(),
    )


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    """构建 FastAPI 应用。

    先注册自定义首页覆盖框架默认页：Starlette 按注册顺序匹配，先注册的 "/"
    会命中，后注册的默认极简页即成为死路由。
    """

    s = settings or Settings.from_env()
    app = FastAPI(title="Analytics Agent")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return INDEX_HTML

    register_chat_routes(app, ChatHandler(create_agent(s)), {})

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
