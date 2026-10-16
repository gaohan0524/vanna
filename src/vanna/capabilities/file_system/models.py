"""
File system capability models.

This module contains data models for file system operations.

本模块定义文件系统能力的数据模型：文件搜索结果（FileSearchMatch）与命令执行结果
（CommandResult）。
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class FileSearchMatch:
    """Represents a single search result within a file system.

    单条文件搜索结果：命中路径 + 可选的内容片段（snippet）。
    """

    path: str
    snippet: Optional[str] = None


@dataclass
class CommandResult:
    """Represents the result of executing a shell command.

    一次 shell 命令的执行结果：标准输出、错误输出与退出码（returncode）。
    """

    stdout: str
    stderr: str
    returncode: int
