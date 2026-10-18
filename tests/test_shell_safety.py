"""
Shell tool safety tests (P0-B).

验证 run_bash / run_command 不再经过 shell：无法用 ``;`` / ``|`` / ``&`` 链接命令，
支持可执行文件白名单，pip install 拒绝覆盖安装源。
"""

import sys

import pytest

from vanna.core import User
from vanna.core.tool import ToolContext
from vanna.integrations.local.agent_memory import DemoAgentMemory
from vanna.integrations.local.file_system import LocalFileSystem as LocalFS
from vanna.tools.file_system import LocalFileSystem as ToolsLocalFileSystem
from vanna.tools.python import PipInstallArgs, PipInstallTool


def _ctx() -> ToolContext:
    return ToolContext(
        user=User(id="u"),
        conversation_id="c",
        request_id="r",
        agent_memory=DemoAgentMemory(max_items=1),
        metadata={},
    )


async def test_run_command_executes_without_shell(tmp_path):
    fs = ToolsLocalFileSystem(working_directory=str(tmp_path))
    result = await fs.run_command([sys.executable, "--version"], _ctx())
    assert result.returncode == 0
    assert "Python" in result.stdout


async def test_run_bash_does_not_allow_shell_chaining(tmp_path):
    fs = ToolsLocalFileSystem(working_directory=str(tmp_path))
    command = f"{sys.executable} --version; echo INJECTED_XYZ"
    result = await fs.run_bash(command, _ctx())

    assert "INJECTED_XYZ" not in result.stdout
    assert "INJECTED_XYZ" not in result.stderr
    # '--version;' 不是合法参数，进程应以非 0 退出
    assert result.returncode != 0


async def test_local_file_system_run_bash_no_shell(tmp_path):
    fs = LocalFS(working_directory=str(tmp_path))
    command = f"{sys.executable} --version & echo INJECTED_XYZ"
    result = await fs.run_bash(command, _ctx())

    assert "INJECTED_XYZ" not in result.stdout
    assert "INJECTED_XYZ" not in result.stderr


async def test_command_allowlist(tmp_path):
    fs = ToolsLocalFileSystem(
        working_directory=str(tmp_path), command_allowlist=["python"]
    )
    result = await fs.run_command([sys.executable, "--version"], _ctx())
    assert result.returncode == 0

    with pytest.raises(PermissionError):
        await fs.run_command(["evil", "arg"], _ctx())


async def test_pip_install_rejects_index_override():
    tool = PipInstallTool()
    result = await tool.execute(
        _ctx(),
        PipInstallArgs(
            packages=["requests"],
            extra_args=["--index-url", "https://evil.example.com"],
        ),
    )
    assert result.success is False
    assert "not allowed" in (result.result_for_llm or "")
