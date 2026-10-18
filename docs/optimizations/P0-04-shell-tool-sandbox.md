# P0-04 shell 工具命令注入与供应链防护

- **对应问题**：`run_bash` / python 工具经 shell 执行，可注入任意命令；`pip_install` 可覆盖安装源
- **优先级**：P0（安全）
- **状态**：已完成
- **提交**：`45f5003 fix: 消除 shell 工具命令注入并收紧 pip 供应链（P0-B）`

---

## 1. 问题背景

优化前，两处 `LocalFileSystem.run_bash` 用 `asyncio.create_subprocess_shell(command, cwd=user_dir)`：

```python
process = await asyncio.create_subprocess_shell(
    command, stdout=PIPE, stderr=PIPE, cwd=str(user_dir),
)
```

`tools/python.py` 还用 `shlex.quote`（POSIX 语义）拼命令后再交给 shell：

- 隔离仅靠 `cwd`，`cd /` 即逃逸；
- `;` `|` `&` `$(...)` 等 shell 特性可链接任意命令（命令注入）；
- Windows 下走 `cmd.exe`，`shlex.quote` 的单引号不生效，注入面更大；
- `PipInstallTool.extra_args` 可传 `--index-url` / `--trusted-host` 覆盖 pip 源（供应链投毒）。

---

## 2. 主流方案参考

| 方案 | 做法 |
|---|---|
| OpenAI Codex / Claude Code / Gemini CLI | 沙箱容器 + 命令白名单 + 不经过 shell（直接 exec argv） |
| LangChain `ShellTool` | 允许禁用 shell、参数校验、可选超时 |
| 供应链安全 | 固定/签名源，禁止运行时覆盖 `--index-url`/`--trusted-host` |

核心原则：**执行外部命令时不用 shell，用 `exec` + argv 列表**；危险工具默认挂管理员权限；包安装固定可信源。

---

## 3. 方案设计

### 3.1 不经过 shell

`run_bash(command)` 先用 `shlex.split` 拆成 argv，再走新的 `run_command(argv)`，后者用 `create_subprocess_exec(*argv)`（等价 `shell=False`）：

```python
async def run_bash(self, command, context, *, timeout=None):
    argv = shlex.split(command, posix=(os.name != "nt"))
    return await self.run_command(argv, context, timeout=timeout)

async def run_command(self, argv, context, *, timeout=None):
    process = await asyncio.create_subprocess_exec(
        *argv, stdin=DEVNULL, stdout=PIPE, stderr=PIPE, cwd=str(user_dir),
    )
    ...
```

- `;` / `|` / `&` 不再是控制字符，只是普通 token；
- `stdin=DEVNULL` 防止子进程阻塞等待输入；
- `FileSystem` 抽象新增 `run_command`（带默认实现退化为 `run_bash`，兼容自定义后端）。

### 3.2 可执行白名单

`LocalFileSystem(..., command_allowlist=["python", "pip"])`：命中时按 argv[0] 的 basename（自动去 `.exe`）校验，不在白名单即 `PermissionError`。

### 3.3 python 工具直接传 argv

`RunPythonFileTool` / `PipInstallTool` 不再 `shlex.quote` 拼串，直接调 `run_command([...])`，彻底消除「转义—再拆解」的往返与 Windows 差异。

### 3.4 供应链防护

`PipInstallTool` 拒绝 `--index-url`/`-i`/`--extra-index-url`/`--trusted-host`/`--no-index`/`--find-links`/`-f` 等可覆盖源的参数。

### 3.5 权限默认收紧

示例 `coding_agent_example.py` 改为 `register_local_tool(tool, access_groups=["admin"])`（顺带修复了原 `tool_registry.register` 不存在导致的崩溃）。

---

## 4. 代码变动清单

| 文件 | 改动 |
|---|---|
| `tools/file_system.py` | `run_bash` 拆 argv；新增 `run_command`；`LocalFileSystem` 支持 `command_allowlist` |
| `integrations/local/file_system.py` | 同上（非阻塞版） |
| `tools/python.py` | python/pip 工具改用 `run_command`，删 `_quote_command`；pip 拒绝覆盖源参数 |
| `examples/coding_agent_example.py` | 工具改 `register_local_tool(..., access_groups=["admin"])` |
| `tests/test_shell_safety.py` | 新增 5 个用例 |

### 关键代码

```python
async def run_command(self, argv, context, *, timeout=None):
    if self.command_allowlist:
        executable = Path(argv[0]).name.lower()
        if executable.endswith(".exe"):
            executable = executable[:-4]
        if executable not in {n.lower() for n in self.command_allowlist}:
            raise PermissionError(f"Command not allowed: {argv[0]}")
    process = await asyncio.create_subprocess_exec(
        *argv, stdin=DEVNULL, stdout=PIPE, stderr=PIPE, cwd=str(user_dir),
    )
```

---

## 5. 验证

`tests/test_shell_safety.py`（5 passed）：

- `run_command` 直接 exec argv；
- `run_bash` 用 `;`/`&` 无法链接第二条命令（无注入输出，`--version;` 非法参数退出非 0）；
- 白名单命中放行、未命中抛 `PermissionError`；
- `PipInstallTool` 拒绝 `--index-url`。

---

## 6. 兼容性与注意事项

1. **行为变化**：`run_bash` 不再支持 shell 特性（管道、重定向、变量展开、`&&`/`||` 等）。需要多步命令请拆成多次 `run_command`，或用 `command_allowlist` 精确放行所需可执行文件。
2. **默认仍不设白名单**：`command_allowlist=None` 时任何可执行文件都能跑（但无 shell）；生产建议显式设置。
3. **`stdin=DEVNULL`**：需要交互式输入的脚本无法读 stdin（这是有意为之）。
4. **Windows**：`shlex.split(posix=False)` 对带引号参数的处理与 POSIX 不同，复杂参数建议直接调用 `run_command(argv)`。

---

## 7. 后续可扩展点

- 接容器/隔离沙箱（docker、nsjail）做文件系统与网络隔离；
- 命令黑名单（如 `rm -rf /`、`dd`）作为白名单的补充；
- 为 python/pip 工具增加默认超时；
- pip 安装源改为固定可信源（环境变量注入，而非运行时参数）。

---

## 附：文件

- 修改：`tools/file_system.py`、`tools/python.py`、`integrations/local/file_system.py`、`examples/coding_agent_example.py`
- 测试：`tests/test_shell_safety.py`
