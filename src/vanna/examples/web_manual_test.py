"""
手动 Web 测试入口：把 Agent 跑起来，用浏览器对话验证 Text2SQL 效果。

所有连接参数与模型参数都直接读根目录 ``.env``（与 analytics_agent 使用同一套
变量名，不加额外前缀），脚本只负责把这些值接到 Agent 上。

与 examples/test.py 的差异：
1. LLM 由 ``LLM_PROVIDER`` 决定（当前 .env 为 deepseek），DeepSeek 走官方 OpenAI
   兼容端点；密钥从 ``DEEPSEEK_API_KEY`` 读取，不写进代码。
2. 不注册任何记忆工具（save_question_tool_args / search_saved_correct_tool_uses /
   save_text_memory），也不接向量库 —— 每个问题都是「纯裸跑」，方便观察模型本身
   生成 SQL 的能力。
3. Agent 的 agent_memory 仍是必填参数，这里只放一个空的内存实现占位，并显式关掉
   记忆检索（DefaultLlmContextEnhancer(None)），保证系统提示词里不含历史记忆。
4. 自定义了首页：框架自带的极简页不加载 Plotly，chart 组件只会打印
   ``[图表] plotly``；这里换成带 Plotly 的页面，可以直接看到柱状图/折线图。

运行：
    python src/vanna/examples/web_manual_test.py
    # 或
    python -m vanna.examples.web_manual_test

然后浏览器打开 http://localhost:8000 ，直接在对话框里提问即可。

读取的 .env 变量：
    CH_HOST / CH_PORT / CH_DATABASE / CH_USER / CH_PASSWORD /
    CH_TIMEOUT_S（单条 SQL 超时）/ CH_MAX_ROWS（单条 SQL 行数上限）
    LLM_PROVIDER（deepseek | ollama）/ LLM_MODEL / LLM_TIMEOUT_S /
    DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL /
    OLLAMA_HOST / LLM_NUM_CTX / LLM_MAX_PREDICT（仅 provider=ollama）
    AGENT_MAX_ITERATIONS（单轮工具调用上限）
真实环境变量优先于 .env，便于临时覆盖。
"""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from vanna import Agent, AgentConfig, User
from vanna.core.enhancer import DefaultLlmContextEnhancer
from vanna.core.llm import LlmService
from vanna.core.registry import ToolRegistry
from vanna.core.user import RequestContext, UserResolver
from vanna.integrations.clickhouse import ClickHouseRunner
from vanna.integrations.deepseek import DeepSeekLlmService
from vanna.integrations.local import LocalFileSystem
from vanna.integrations.local.agent_memory import DemoAgentMemory
from vanna.integrations.ollama import OllamaLlmService
from vanna.servers.base import ChatHandler
from vanna.servers.fastapi.routes import register_chat_routes
from vanna.tools import RunSqlTool, VisualizeDataTool

# --------------------------------------------------------------------- 配置区

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _load_dotenv(path: Path) -> None:
    """加载 ``KEY=VALUE`` 形式的 .env；已存在的环境变量优先，不覆盖。

    与 analytics_agent/config.py 保持一致：不依赖第三方包，密码里允许出现 ``#``
    （因此不剥离行内注释）。
    """
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    return int(raw) if raw else default


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    return float(raw) if raw else default


_load_dotenv(PROJECT_ROOT / ".env")

# ---- LLM ----
LLM_PROVIDER = _env("LLM_PROVIDER", "deepseek").lower()
LLM_MODEL = _env("LLM_MODEL", "deepseek-chat")
LLM_TIMEOUT_S = _env_float("LLM_TIMEOUT_S", 240.0)
DEEPSEEK_API_KEY = _env("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
OLLAMA_HOST = _env("OLLAMA_HOST", "http://localhost:11434")
LLM_NUM_CTX = _env_int("LLM_NUM_CTX", 8192)
LLM_MAX_PREDICT = _env_int("LLM_MAX_PREDICT", 512)
# 单轮工具调用上限：Text2SQL 的常见路径是「查错 → 改 SQL → 重试 → 画图」，
# 8 次在模型反复报错时容易被截断，兜底默认给到 16（.env 里的值优先）。
AGENT_MAX_ITERATIONS = _env_int("AGENT_MAX_ITERATIONS", 16)

# ---- ClickHouse ----
CH_HOST = _env("CH_HOST", "localhost")
CH_PORT = _env_int("CH_PORT", 8123)
CH_DATABASE = _env("CH_DATABASE", "shop")
CH_USER = _env("CH_USER", "readonly")
CH_PASSWORD = _env("CH_PASSWORD")
CH_TIMEOUT_S = _env_int("CH_TIMEOUT_S", 30)
CH_MAX_ROWS = _env_int("CH_MAX_ROWS", 10_000)

# ---- 服务本身 ----
HOST = "0.0.0.0"
PORT = 8000

# run_sql 写出的 CSV 与 visualize_data 读取的 CSV 必须落在同一目录；
# 每个用户按 sha256(user.id)[:16] 自动分子目录。固定放项目根下的独立目录，
# 避免把 query_results_*.csv 散落到仓库根目录。
WORK_DIR = PROJECT_ROOT / ".vanna_web_test_data"

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

    var el = chartEls[id];
    if (!el) {
      var card = document.createElement('div');
      card.className = 'chart-card';
      if (title) {
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


class SimpleUserResolver(UserResolver):
    """从 cookie 读取邮箱并据此分配分组。

    默认不带 cookie 时按 admin 处理，这样页面上能看到模型生成的 SQL 与图表
    （默认 UiFeatures 中 tool_arguments 仅 admin 可见）。想模拟普通用户时，
    在浏览器里设置 cookie ``vanna_email=guest@example.com`` 即可。
    """

    async def resolve_user(self, request_context: RequestContext) -> User:
        email = request_context.get_cookie("vanna_email") or "admin@example.com"
        group = "admin" if email.startswith("admin") else "user"
        return User(id=email, email=email, group_memberships=[group])


def create_llm() -> LlmService:
    """按 .env 的 LLM_PROVIDER 创建 LLM 服务。"""

    # Text2SQL 需要确定性输出，温度统一固定 0.0
    if LLM_PROVIDER == "deepseek":
        if not DEEPSEEK_API_KEY:
            raise SystemExit(
                "未检测到 DEEPSEEK_API_KEY：请在根目录 .env 中配置后再启动。"
            )
        return DeepSeekLlmService(
            model=LLM_MODEL,
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
            temperature=0.0,
            timeout=LLM_TIMEOUT_S,
        )

    if LLM_PROVIDER == "ollama":
        return OllamaLlmService(
            model=LLM_MODEL,
            host=OLLAMA_HOST,
            num_ctx=LLM_NUM_CTX,
            temperature=0.0,
            timeout=LLM_TIMEOUT_S,
            num_predict=LLM_MAX_PREDICT,
        )

    raise SystemExit(
        f"暂不支持 LLM_PROVIDER={LLM_PROVIDER!r}（本脚本可选 deepseek / ollama）"
    )


def create_agent() -> Agent:
    """创建用于手动 Web 测试的 Agent（只有查数与画图，不接入记忆）。"""

    llm = create_llm()

    # 共享同一个 FileSystem 实例：run_sql 写 CSV、visualize_data 读 CSV
    file_system = LocalFileSystem(working_directory=str(WORK_DIR))

    sql_runner = ClickHouseRunner(
        host=CH_HOST,
        database=CH_DATABASE,
        user=CH_USER,
        password=CH_PASSWORD,
        port=CH_PORT,
        # 同步驱动默认无超时，必须让 ClickHouse 自己限时
        query_settings={"max_execution_time": CH_TIMEOUT_S},
    )

    tool_registry = ToolRegistry()
    tool_registry.register_local_tool(
        RunSqlTool(
            sql_runner=sql_runner,
            file_system=file_system,
            timeout_seconds=CH_TIMEOUT_S,
            max_rows=CH_MAX_ROWS,
        ),
        access_groups=["admin", "user"],
    )
    tool_registry.register_local_tool(
        VisualizeDataTool(file_system=file_system),
        access_groups=["admin", "user"],
    )

    return Agent(
        llm_service=llm,
        tool_registry=tool_registry,
        user_resolver=SimpleUserResolver(),
        # 必填参数，这里只放空的内存实现占位；下面把 enhancer 的记忆检索也关掉，
        # 因此既不会写入记忆，也不会向系统提示词注入任何记忆内容。
        agent_memory=DemoAgentMemory(max_items=1000),
        llm_context_enhancer=DefaultLlmContextEnhancer(None),
        config=AgentConfig(
            stream_responses=True,
            max_tool_iterations=AGENT_MAX_ITERATIONS,
        ),
    )


def create_app() -> FastAPI:
    """构建 FastAPI 应用。

    先注册自定义首页覆盖框架默认页：Starlette 按注册顺序匹配，先注册的 "/"
    会命中，后注册的默认极简页即成为死路由。
    """

    app = FastAPI(title="Vanna 手动 Web 测试")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return INDEX_HTML

    register_chat_routes(app, ChatHandler(create_agent()), {})

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def main() -> None:
    print("=" * 64)
    print(f"LLM        : {LLM_PROVIDER} / {LLM_MODEL}")
    if LLM_PROVIDER == "deepseek":
        print(f"端点       : {DEEPSEEK_BASE_URL}")
        print(f"API Key    : {'已配置' if DEEPSEEK_API_KEY else '未配置'}")
    else:
        print(f"端点       : {OLLAMA_HOST}")
    print(f"ClickHouse : {CH_USER}@{CH_HOST}:{CH_PORT}/{CH_DATABASE}")
    print("工具       : run_sql, visualize_data（未接入记忆）")
    print(f"工具上限   : 单轮最多 {AGENT_MAX_ITERATIONS} 次工具调用")
    print("图表       : 已启用 Plotly 渲染（CDN: cdn.plot.ly）")
    print(f"工作目录   : {WORK_DIR}")
    print(f"浏览器打开 : http://localhost:{PORT}")
    print("=" * 64)
    uvicorn.run(create_app(), host=HOST, port=PORT)


if __name__ == "__main__":
    main()
