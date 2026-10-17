"""
HTML templates for Vanna Agents servers.
"""

from typing import Optional


def get_vanna_component_script(
    dev_mode: bool = False,
    static_path: str = "/static",
    cdn_url: str = "https://img.vanna.ai/vanna-components.js",
) -> str:
    """Get the script tag for loading Vanna web components.

    Args:
        dev_mode: If True, load from local static files
        static_path: Path to static assets in dev mode
        cdn_url: CDN URL for production

    Returns:
        HTML script tag for loading components
    """
    if dev_mode:
        return (
            f'<script type="module" src="{static_path}/vanna-components.js"></script>'
        )
    else:
        return f'<script type="module" src="{cdn_url}"></script>'


def get_rich_index_html(
    dev_mode: bool = False,
    static_path: str = "/static",
    cdn_url: str = "https://img.vanna.ai/vanna-components.js",
    api_base_url: str = "",
) -> str:
    """Generate the rich (Tailwind + <vanna-chat> web component) index HTML.

    Args:
        dev_mode: If True, load components from local static files
        static_path: Path to static assets in dev mode
        cdn_url: CDN URL for production components
        api_base_url: Base URL for API endpoints

    Returns:
        Complete HTML page as string

    注意：该「富前端」默认已不再使用，默认首页改为 get_simple_chat_html 提供的极简
    对话框。保留本函数供仍想使用 Web Component（<vanna-chat>）的场景显式调用。
    """
    component_script = get_vanna_component_script(dev_mode, static_path, cdn_url)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Vanna Agents Chat</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Roboto+Slab:wght@400;500;600;700&family=Space+Grotesk:wght@300;400;500;600;700&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {{
            theme: {{
                extend: {{
                    colors: {{
                        'vanna-navy': '#023d60',
                        'vanna-cream': '#e7e1cf',
                        'vanna-teal': '#15a8a8',
                        'vanna-orange': '#fe5d26',
                        'vanna-magenta': '#bf1363',
                    }},
                    fontFamily: {{
                        'sans': ['Space Grotesk', 'ui-sans-serif', 'system-ui'],
                        'serif': ['Roboto Slab', 'ui-serif', 'Georgia'],
                        'mono': ['Space Mono', 'ui-monospace', 'monospace'],
                    }}
                }}
            }}
        }}
    </script>
    <style>
        body {{
            background: linear-gradient(to bottom, #e7e1cf, #ffffff, #e7e1cf);
            min-height: 100vh;
            position: relative;
            overflow-x: hidden;
        }}

        /* Background decorations matching landing page */
        body::before {{
            content: '';
            position: fixed;
            inset: 0;
            pointer-events: none;
            z-index: 0;
            /* Radial gradients with brand colors */
            background:
                radial-gradient(circle at top left, rgba(21, 168, 168, 0.12), transparent 60%),
                radial-gradient(circle at bottom right, rgba(254, 93, 38, 0.08), transparent 65%);
        }}

        body::after {{
            content: '';
            position: fixed;
            inset: 0;
            pointer-events: none;
            z-index: 0;
            /* Dot pattern with retro computing aesthetic */
            background-image: radial-gradient(circle at 2px 2px, rgba(2, 61, 96, 0.3) 1px, transparent 0);
            background-size: 32px 32px;
            /* Grid overlay */
            background-image:
                radial-gradient(circle at 2px 2px, rgba(2, 61, 96, 0.3) 1px, transparent 0),
                linear-gradient(rgba(2, 61, 96, 0.1) 1px, transparent 1px),
                linear-gradient(90deg, rgba(2, 61, 96, 0.1) 1px, transparent 1px);
            background-size: 32px 32px, 100px 100px, 100px 100px;
        }}

        /* Ensure content is above background */
        body > * {{
            position: relative;
            z-index: 1;
        }}

        vanna-chat {{
            width: 100%;
            height: 100%;
            display: block;
        }}
    </style>
    {component_script}
</head>
<body>
    <div class="max-w-6xl mx-auto p-5">
        <!-- Header -->
        <div class="text-center mb-8">
            <h1 class="text-4xl font-bold text-vanna-navy mb-2 font-serif">Vanna Agents</h1>
            <p class="text-lg font-mono font-bold text-vanna-teal mb-4">DATA-FIRST AGENTS</p>
            <p class="text-slate-600 mb-4">Interactive AI Assistant powered by Vanna Agents Framework</p>
            <a href="javascript:window.location='view-source:'+window.location.href" class="inline-flex items-center gap-2 px-4 py-2 bg-vanna-teal text-white text-sm font-medium rounded-lg hover:bg-vanna-navy transition">
                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4"/>
                </svg>
                View Page Source
            </a>
        </div>

        {('    <div class="bg-vanna-orange/10 border border-vanna-orange/30 rounded-lg p-3 mb-5 text-vanna-orange text-sm font-medium">📦 Development Mode: Loading components from local assets</div>' if dev_mode else "")}

        <!-- Login Form -->
        <div id="loginContainer" class="max-w-md mx-auto mb-10 bg-white p-8 rounded-xl shadow-lg border border-vanna-teal/30">
            <div class="text-center mb-6">
                <h2 class="text-2xl font-semibold text-vanna-navy mb-2 font-serif">Login to Continue</h2>
                <p class="text-sm text-slate-600">Select your email to access the chat</p>
            </div>

            <div class="mb-5">
                <label for="emailInput" class="block mb-2 text-sm font-medium text-vanna-navy">Email Address</label>
                <select
                    id="emailInput"
                    class="w-full px-4 py-3 text-sm border border-vanna-teal/30 rounded-lg focus:outline-none focus:ring-2 focus:ring-vanna-teal focus:border-transparent bg-white"
                >
                    <option value="">Select an email...</option>
                    <option value="admin@example.com">admin@example.com</option>
                    <option value="user@example.com">user@example.com</option>
                </select>
            </div>

            <button id="loginButton" class="w-full px-4 py-3 bg-vanna-teal text-white text-sm font-medium rounded-lg hover:bg-vanna-navy focus:outline-none focus:ring-2 focus:ring-vanna-teal focus:ring-offset-2 transition disabled:bg-gray-400 disabled:cursor-not-allowed">
                Continue
            </button>

            <div class="mt-5 p-3 bg-vanna-teal/10 border-l-4 border-vanna-teal rounded text-xs text-vanna-navy leading-relaxed">
                <strong>Demo Mode:</strong> This is a frontend-only authentication demo.
                Your email will be stored as a cookie and automatically sent with all API requests.
            </div>
        </div>

        <!-- Logged In Status (hidden by default) -->
        <div id="loggedInStatus" class="hidden text-center p-4 bg-vanna-teal/10 border border-vanna-teal/30 rounded-lg mb-5">
            Logged in as <span id="loggedInEmail" class="font-semibold text-vanna-navy"></span>
            <br>
            <button id="logoutButton" class="mt-2 px-3 py-1.5 bg-vanna-navy text-white text-xs rounded hover:bg-vanna-teal transition">
                Logout
            </button>
        </div>

        <!-- Chat Container (hidden by default) -->
        <div id="chatSections" class="hidden">
            <div class="bg-white rounded-xl shadow-lg h-[600px] overflow-hidden border border-vanna-teal/30">
                <vanna-chat
                    api-base="{api_base_url}"
                    sse-endpoint="{api_base_url}/api/vanna/v2/chat_sse"
                    ws-endpoint="{api_base_url}/api/vanna/v2/chat_websocket"
                    poll-endpoint="{api_base_url}/api/vanna/v2/chat_poll">
                </vanna-chat>
            </div>

            <div class="mt-8 p-5 bg-white rounded-lg shadow border border-vanna-teal/30">
                <h3 class="text-lg font-semibold text-vanna-navy mb-3 font-serif">API Endpoints</h3>
                <ul class="space-y-2">
                    <li class="p-2 bg-vanna-cream/50 rounded font-mono text-sm">
                        <span class="font-bold text-vanna-teal mr-2">POST</span>{api_base_url}/api/vanna/v2/chat_sse - Server-Sent Events streaming
                    </li>
                    <li class="p-2 bg-vanna-cream/50 rounded font-mono text-sm">
                        <span class="font-bold text-vanna-teal mr-2">WS</span>{api_base_url}/api/vanna/v2/chat_websocket - WebSocket real-time chat
                    </li>
                    <li class="p-2 bg-vanna-cream/50 rounded font-mono text-sm">
                        <span class="font-bold text-vanna-teal mr-2">POST</span>{api_base_url}/api/vanna/v2/chat_poll - Request/response polling
                    </li>
                    <li class="p-2 bg-vanna-cream/50 rounded font-mono text-sm">
                        <span class="font-bold text-vanna-teal mr-2">GET</span>{api_base_url}/health - Health check
                    </li>
                </ul>
            </div>
        </div>
    </div>

    <script>
        // Cookie helpers
        const getCookie = (name) => {{
            const value = `; ${{document.cookie}}`;
            const parts = value.split(`; ${{name}}=`);
            return parts.length === 2 ? parts.pop().split(';').shift() : null;
        }};

        const setCookie = (name, value) => {{
            const expires = new Date(Date.now() + 365 * 864e5).toUTCString();
            document.cookie = `${{name}}=${{value}}; expires=${{expires}}; path=/; SameSite=Lax`;
        }};

        const deleteCookie = (name) => {{
            document.cookie = `${{name}}=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;`;
        }};

        // Login/Logout
        document.addEventListener('DOMContentLoaded', () => {{
            const email = getCookie('vanna_email');

            // Check if already logged in
            if (email) {{
                loginContainer.classList.add('hidden');
                loggedInStatus.classList.remove('hidden');
                chatSections.classList.remove('hidden');
                loggedInEmail.textContent = email;
            }}

            // Login button
            loginButton.addEventListener('click', () => {{
                const email = emailInput.value.trim();
                if (!email) {{
                    alert('Please select an email address');
                    return;
                }}
                setCookie('vanna_email', email);
                loginContainer.classList.add('hidden');
                loggedInStatus.classList.remove('hidden');
                chatSections.classList.remove('hidden');
                loggedInEmail.textContent = email;
            }});

            // Logout button
            logoutButton.addEventListener('click', () => {{
                deleteCookie('vanna_email');
                loginContainer.classList.remove('hidden');
                loggedInStatus.classList.add('hidden');
                chatSections.classList.add('hidden');
                emailInput.value = '';
            }});

            // Enter key
            emailInput.addEventListener('keypress', (e) => {{
                if (e.key === 'Enter') loginButton.click();
            }});
        }});
    </script>

    <script>
        // Artifact demo event listener
        document.addEventListener('DOMContentLoaded', () => {{
            const vannaChat = document.querySelector('vanna-chat');

            if (vannaChat) {{
                // Add artifact event listener to demonstrate external rendering
                vannaChat.addEventListener('artifact-opened', (event) => {{
                    const {{ artifactId, type, title, trigger }} = event.detail;

                    console.log('🎨 Artifact Event:', {{ artifactId, type, title, trigger }});

                    // For demo: open all artifacts externally
                    setTimeout(() => {{
                        const newWindow = window.open('', '_blank', 'width=900,height=700');
                        if (newWindow) {{
                            newWindow.document.write(event.detail.getStandaloneHTML());
                            newWindow.document.close();
                            newWindow.document.title = title || 'Vanna Artifact';
                            console.log(`📱 Opened ${{title}} in new window`);
                        }}
                    }}, 100);

                    // Prevent default in-chat rendering
                    event.detail.preventDefault();
                    console.log('✋ Showing placeholder in chat instead of full artifact');
                }});

                console.log('🎯 Artifact demo mode: All artifacts will open externally');
            }}
        }});

        // Fallback if web component doesn't load
        if (!customElements.get('vanna-chat')) {{
            setTimeout(() => {{
                if (!customElements.get('vanna-chat')) {{
                    document.querySelector('vanna-chat').innerHTML = `
                        <div class="p-10 text-center text-gray-600">
                            <h3 class="text-xl font-semibold mb-2">Vanna Chat Component</h3>
                            <p class="mb-2">Web component failed to load. Please check your connection.</p>
                            <p class="text-sm text-gray-400">
                                {("Loading from: local static assets" if dev_mode else f"Loading from: {cdn_url}")}
                            </p>
                        </div>
                    `;
                }}
            }}, 2000);
        }}
    </script>
</body>
</html>"""


def get_simple_chat_html(api_base_url: str = "") -> str:
    """Generate the default, dependency-free chat page with a stop/cancel button.

    这是所有 *.py 示例共用的默认前端：不依赖 Tailwind / Web Component / CDN，只做三件
    事——显示消息、渲染 SQL 结果表格、提交问题。直接 POST 到 chat_sse 端点并用
    fetch + ReadableStream 解析 SSE（EventSource 只支持 GET，故手动解析）。

    内置「停止」按钮：点击后通过 AbortController 中断 fetch，浏览器随之关闭 TCP 连接，
    服务端 chat_sse 的 disconnect watcher 检测到断开后取消在途的 LLM 调用（token 级中断）。
    """
    sse_endpoint = (api_base_url or "") + "/api/vanna/v2/chat_sse"

    html = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>智能体 测试台</title>
</head>
<body>

<h3>智能体 测试台</h3>

<div>
  <label>身份邮箱：
    <input id="email" type="text" value="guest@example.com" size="32">
  </label>
</div>

<hr>

<div id="messages"></div>

<hr>

<form id="form">
  <input id="input" type="text" size="70" autocomplete="off" placeholder="输入问题，回车发送">
  <button type="submit">发送</button>
  <button id="stop" type="button" disabled>停止</button>
</form>

<script>
(function () {
  var messagesEl = document.getElementById('messages');
  var formEl = document.getElementById('form');
  var inputEl = document.getElementById('input');
  var emailEl = document.getElementById('email');
  var stopBtn = document.getElementById('stop');

  var conversationId = null;
  var busy = false;
  var abortController = null;
  var currentAiTextId = null;
  var currentAiTextEl = null;

  // 追加一行文本，prefix 为角色前缀（如 "AI: "）
  function appendLine(prefix, text) {
    var div = document.createElement('div');
    if (prefix) {
      var strong = document.createElement('strong');
      strong.textContent = prefix;
      div.appendChild(strong);
    }
    var lines = String(text).split('\n');
    for (var i = 0; i < lines.length; i++) {
      if (i > 0) {
        div.appendChild(document.createElement('br'));
      }
      div.appendChild(document.createTextNode(lines[i]));
    }
    messagesEl.appendChild(div);
    return div;
  }

  // 更新某个 AI 文本行（打字机效果：用累积文本覆盖，而非新增一行）
  function setAiText(el, text) {
    el.textContent = '';
    var strong = document.createElement('strong');
    strong.textContent = 'AI: ';
    el.appendChild(strong);
    var lines = String(text).split('\n');
    for (var i = 0; i < lines.length; i++) {
      if (i > 0) {
        el.appendChild(document.createElement('br'));
      }
      el.appendChild(document.createTextNode(lines[i]));
    }
  }

  // 渲染 SQL 结果集：rows 为对象数组，columns 为列名数组
  function appendTable(rows, columns) {
    if (!rows || rows.length === 0) {
      appendLine('[数据] ', '(空结果)');
      return;
    }
    var cols = (columns && columns.length) ? columns : Object.keys(rows[0]);
    var table = document.createElement('table');
    table.setAttribute('border', '1');

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
  }

  // 处理单个流式分片。分片结构为 {rich, simple, conversation_id, request_id, timestamp}，
  // 其中 rich 是组件序列化结果：顶层是 id/type/lifecycle 等，组件字段统一放在 rich.data 下。
  function handleChunk(chunk) {
    if (!chunk) return;
    if (chunk.conversation_id) {
      conversationId = chunk.conversation_id;
    }

    var rich = chunk.rich || {};
    var data = rich.data || {};

    switch (rich.type) {
      case 'text':
        // token 级流式：create 新建一行，update 用同 id 更新同一行（打字机效果）
        if (rich.lifecycle === 'create' || currentAiTextId !== rich.id) {
          currentAiTextId = rich.id;
          currentAiTextEl = appendLine('AI: ', data.content || '');
        } else if (rich.lifecycle === 'update' && currentAiTextEl) {
          setAiText(currentAiTextEl, data.content || '');
        }
        break;
      case 'dataframe':
        appendTable(data.data || [], data.columns || []);
        break;
      case 'chart':
        // 无样式页面不引入 Plotly，仅提示图表已生成
        appendLine('[图表] ', data.chart_type || 'chart');
        break;
      case 'status_bar_update':
        if (data.message) {
          appendLine('[状态] ', data.message);
        }
        break;
      case 'status_card':
        appendLine('[工具] ', (data.title || '') + (data.description ? ' - ' + data.description : ''));
        // 工具调用参数（Text2SQL 中即 SQL 语句）默认仅 admin 组可见，
        // 普通用户根本收不到带 metadata 的这张卡片，因此这里直接展示即可。
        if (data.metadata && Object.keys(data.metadata).length > 0) {
          appendLine('[参数] ', JSON.stringify(data.metadata));
        }
        break;
      case 'notification':
        appendLine('[提示] ', data.message || data.title || '');
        break;
      default:
        // 任务追踪、输入框状态等组件在极简页面中忽略
        break;
    }
  }

  function setBusy(b) {
    busy = b;
    inputEl.disabled = b;
    stopBtn.disabled = !b;
  }

  async function send(message) {
    if (busy) return;
    setBusy(true);

    // 把身份写入 cookie，SimpleUserResolver 会读取它
    document.cookie = 'vanna_email=' + encodeURIComponent(emailEl.value || 'guest@example.com') + ';path=/';

    appendLine('我: ', message);

    var payload = { message: message };
    if (conversationId) {
      payload.conversation_id = conversationId;
    }

    abortController = new AbortController();

    try {
      var resp = await fetch('__SSE_ENDPOINT__', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: abortController.signal
      });

      if (!resp.ok || !resp.body) {
        appendLine('[错误] ', 'HTTP ' + resp.status);
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
        var parts = buffer.split('\n\n');
        buffer = parts.pop();

        for (var i = 0; i < parts.length; i++) {
          var line = parts[i].trim();
          if (line.indexOf('data:') !== 0) continue;
          var raw = line.slice(5).trim();
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
        appendLine('[已停止] ', '生成已停止');
      } else {
        appendLine('[错误] ', String(err));
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
    return html.replace("__SSE_ENDPOINT__", sse_endpoint)


def get_index_html(
    dev_mode: bool = False,
    static_path: str = "/static",
    cdn_url: str = "https://img.vanna.ai/vanna-components.js",
    api_base_url: str = "",
) -> str:
    """Generate the default index HTML (minimal chat page with stop button).

    Args:
        dev_mode: Unused in the minimal page (kept for backward compatibility).
        static_path: Unused in the minimal page (kept for backward compatibility).
        cdn_url: Unused in the minimal page (kept for backward compatibility).
        api_base_url: Base URL for the chat_sse endpoint.

    Returns:
        Complete HTML page as string

    所有 *.py 示例通过 register_chat_routes 注册的 "/" 路由都会调用本函数，因此默认
    使用同一个极简前端。若想用回富前端（<vanna-chat> Web Component），显式调用
    get_rich_index_html 即可。
    """
    return get_simple_chat_html(api_base_url=api_base_url)


# Backward compatibility - default production HTML
INDEX_HTML = get_index_html()
