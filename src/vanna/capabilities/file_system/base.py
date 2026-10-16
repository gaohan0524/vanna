"""
File system capability interface.

This module contains the abstract base class for file system operations.

本模块定义文件系统能力的抽象接口 FileSystem，为工具（如 run_sql 写结果 CSV、
visualize_data 读 CSV）提供「按用户隔离」的文件读写与命令执行能力。具体实现见
integrations/local 的 LocalFileSystem（默认，按用户哈希建目录）。
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, List, Optional

from .models import CommandResult, FileSearchMatch

if TYPE_CHECKING:
    from vanna.core.tool import ToolContext


class FileSystem(ABC):
    """Abstract base class for file system operations.

    文件系统抽象基类。所有方法都以 context（含用户）为作用域，实现应把每个用户
    的文件隔离在各自命名空间，防止跨用户读写。
    """

    @abstractmethod
    async def list_files(self, directory: str, context: "ToolContext") -> List[str]:
        """List files in a directory.

        列出目录下的文件。
        """
        pass

    @abstractmethod
    async def read_file(self, filename: str, context: "ToolContext") -> str:
        """Read the contents of a file.

        读取文件内容。
        """
        pass

    @abstractmethod
    async def write_file(
        self,
        filename: str,
        content: str,
        context: "ToolContext",
        overwrite: bool = False,
    ) -> None:
        """Write content to a file.

        写文件（overwrite 控制是否覆盖已存在文件）。
        """
        pass

    @abstractmethod
    async def exists(self, path: str, context: "ToolContext") -> bool:
        """Check if a file or directory exists.

        判断文件或目录是否存在。
        """
        pass

    @abstractmethod
    async def is_directory(self, path: str, context: "ToolContext") -> bool:
        """Check if a path is a directory.

        判断路径是否为目录。
        """
        pass

    @abstractmethod
    async def search_files(
        self,
        query: str,
        context: "ToolContext",
        *,
        max_results: int = 20,
        include_content: bool = False,
    ) -> List[FileSearchMatch]:
        """Search for files matching a query within the accessible namespace.

        在可访问命名空间内按查询搜索文件（可附带内容）。
        """
        pass

    @abstractmethod
    async def run_bash(
        self,
        command: str,
        context: "ToolContext",
        *,
        timeout: Optional[float] = None,
    ) -> CommandResult:
        """Execute a bash command within the accessible namespace.

        在可访问命名空间内执行 shell 命令，返回 stdout/stderr/退出码。
        """
        pass
