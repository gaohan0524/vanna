"""Python-specific tooling built on top of the file system service.

中文说明：基于文件系统服务构建的 Python 专用工具。
"""

from __future__ import annotations

import shlex
import sys
from typing import Any, List, Optional, Sequence, Type

from pydantic import BaseModel, Field

from vanna.components import (
    UiComponent,
    CardComponent,
    ComponentType,
    NotificationComponent,
    SimpleTextComponent,
)
from vanna.core.tool import Tool, ToolContext, ToolResult

from .file_system import CommandResult, FileSystem, LocalFileSystem

MAX_OUTPUT_LENGTH = 4000


class RunPythonFileArgs(BaseModel):
    """Arguments required to execute a Python file.

    中文：执行 Python 文件所需的参数模型。
    """

    filename: str = Field(
        description="Python file to execute (relative to the workspace root)"
    )
    arguments: Sequence[str] = Field(
        default_factory=list,
        description="Optional arguments to pass to the Python script",
    )
    timeout_seconds: Optional[float] = Field(
        default=None,
        ge=0,
        description="Optional timeout for the command in seconds",
    )


class RunPythonFileTool(Tool[RunPythonFileArgs]):
    """Execute a Python file using the provided file system service.

    中文：使用提供的文件系统服务执行 Python 文件。
    """

    def __init__(self, file_system: Optional[FileSystem] = None):
        self.file_system = file_system or LocalFileSystem()

    @property
    def name(self) -> str:
        return "run_python_file"

    @property
    def description(self) -> str:
        return "Execute a Python file using the workspace interpreter"

    def get_args_schema(self) -> Type[RunPythonFileArgs]:
        return RunPythonFileArgs

    async def execute(
        self, context: ToolContext, args: RunPythonFileArgs
    ) -> ToolResult:
        """执行 Python 文件：先校验文件存在，再通过文件系统运行并返回结果。"""
        # 校验文件是否存在，不存在则直接返回错误结果
        exists = await self.file_system.exists(args.filename, context)
        if not exists:
            message = f"Cannot execute '{args.filename}' because it does not exist."
            return _error_result(message)

        # 拼接命令：当前 Python 解释器 + 文件名 + 附加参数（不经过 shell）
        command_parts = [sys.executable, args.filename, *args.arguments]
        command = shlex.join(command_parts)

        try:
            result = await self.file_system.run_command(
                command_parts,
                context,
                timeout=args.timeout_seconds,
            )
        except TimeoutError as exc:
            return _error_result(str(exc))

        summary = f"Executed python {args.filename} (exit code {result.returncode})."
        success = result.returncode == 0
        return _result_from_command(summary, command, result, success=success)


class PipInstallArgs(BaseModel):
    """Arguments required to run pip install.

    中文：运行 pip install 所需的参数模型。
    """

    packages: List[str] = Field(
        description="Packages (with optional specifiers) to install", min_length=1
    )
    upgrade: bool = Field(
        default=False,
        description="Whether to include --upgrade in the pip invocation",
    )
    extra_args: Sequence[str] = Field(
        default_factory=list,
        description="Additional arguments to pass to pip install",
    )
    timeout_seconds: Optional[float] = Field(
        default=None,
        ge=0,
        description="Optional timeout for the command in seconds",
    )


class PipInstallTool(Tool[PipInstallArgs]):
    """Install Python packages using pip inside the workspace environment.

    中文：在工作区环境中使用 pip 安装 Python 包。
    """

    def __init__(self, file_system: Optional[FileSystem] = None):
        self.file_system = file_system or LocalFileSystem()

    @property
    def name(self) -> str:
        return "pip_install"

    @property
    def description(self) -> str:
        return "Install Python packages using pip"

    def get_args_schema(self) -> Type[PipInstallArgs]:
        return PipInstallArgs

    async def execute(self, context: ToolContext, args: PipInstallArgs) -> ToolResult:
        """执行 pip install：组装命令并通过文件系统运行，返回结果。"""
        # 组装 pip install 命令
        command_parts = [sys.executable, "-m", "pip", "install"]
        if args.upgrade:
            command_parts.append("--upgrade")

        # 供应链防护：禁止通过 extra_args 覆盖 pip 源 / 信任主机 / 离线安装
        forbidden_args = {
            "--index-url",
            "-i",
            "--extra-index-url",
            "--trusted-host",
            "--no-index",
            "--find-links",
            "-f",
        }
        for arg in args.extra_args:
            if arg in forbidden_args:
                return _error_result(
                    f"Refusing pip install: argument '{arg}' is not allowed."
                )

        command_parts.extend(args.packages)
        command_parts.extend(args.extra_args)
        command = shlex.join(command_parts)

        try:
            result = await self.file_system.run_command(
                command_parts,
                context,
                timeout=args.timeout_seconds,
            )
        except TimeoutError as exc:
            return _error_result(str(exc))

        success = result.returncode == 0
        summary = (
            "pip install completed successfully"
            if success
            else f"pip install failed (exit code {result.returncode})."
        )

        return _result_from_command(summary, command, result, success=success)


def create_python_tools(file_system: Optional[FileSystem] = None) -> List[Tool[Any]]:
    """Create Python-specific tools backed by a shared file system service.

    中文：创建由共享文件系统服务支持的 Python 专用工具。
    """

    fs = file_system or LocalFileSystem()
    return [
        RunPythonFileTool(fs),
        PipInstallTool(fs),
    ]


def _truncate(text: str, limit: int = MAX_OUTPUT_LENGTH) -> str:
    """将过长文本截断到指定长度（默认 MAX_OUTPUT_LENGTH）。"""
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}…"


def _result_from_command(
    summary: str,
    command: str,
    result: CommandResult,
    *,
    success: bool = True,
) -> ToolResult:
    """根据命令执行结果构造 ToolResult（包含命令卡片与 LLM 文本）。

    中文：将命令执行的 stdout/stderr 整理为卡片组件，并生成供 LLM 阅读的结果文本。
    """
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()

    blocks: List[str] = [f"$ {command}"]
    if stdout:
        blocks.append("STDOUT:\n" + _truncate(stdout))
    if stderr:
        blocks.append("STDERR:\n" + _truncate(stderr))
    if not stdout and not stderr:
        blocks.append("(no output)")

    content = "\n\n".join(blocks)
    card_status = "success" if success else "error"
    component = CardComponent(
        type=ComponentType.CARD,
        title="Command Result",
        content=content,
        status=card_status,
    )

    return ToolResult(
        success=success,
        result_for_llm=f"{summary}\n\n{content}",
        ui_component=UiComponent(
            rich_component=component,
            simple_component=SimpleTextComponent(text=summary),
        ),
        error=None if success else content,
    )


def _error_result(message: str) -> ToolResult:
    """构造一个失败结果的 ToolResult（通知组件 + 错误信息）。"""
    return ToolResult(
        success=False,
        result_for_llm=message,
        ui_component=UiComponent(
            rich_component=NotificationComponent(
                type=ComponentType.NOTIFICATION,
                level="error",
                message=message,
            ),
            simple_component=SimpleTextComponent(text=message),
        ),
        error=message,
    )
