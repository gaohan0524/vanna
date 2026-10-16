"""
Request context for user resolution.

This module provides the RequestContext model for passing web request
information to UserResolver implementations.

本模块定义 RequestContext：Web 请求到 UserResolver 之间传递信息的标准化载体。它把
cookie / header / IP / 查询参数等原始请求数据整理成结构化对象，使身份解析逻辑与
具体 Web 框架（FastAPI / Flask）解耦——HTTP 层只负责把请求塞进这个对象。
"""

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class RequestContext(BaseModel):
    """Context from a web request for user resolution.

    This structured object replaces raw dictionaries for passing request
    data to UserResolver implementations, making it easier to access
    cookies, headers, and other request metadata.

    Example:
        context = RequestContext(
            cookies={'vanna_email': 'alice@example.com'},
            headers={'Authorization': 'Bearer token'},
            remote_addr='127.0.0.1'
        )
        user = await resolver.resolve_user(context)

    HTTP 层与身份解析层之间的中立数据结构：FastAPI/Flask 各自负责填充，UserResolver
    只依赖本对象，从而在不同 Web 框架间复用同一套身份解析实现。
    """

    # 请求 Cookie，常用于承载身份标识（如 vanna_email）
    cookies: Dict[str, str] = Field(default_factory=dict, description="Request cookies")

    # 请求头，常用于承载 JWT / Authorization 等鉴权信息
    headers: Dict[str, str] = Field(default_factory=dict, description="Request headers")

    # 客户端 IP，可用于 IP 白名单、审计或地域限制
    remote_addr: Optional[str] = Field(default=None, description="Remote IP address")

    # 查询参数，可用于传递一次性身份令牌等
    query_params: Dict[str, str] = Field(
        default_factory=dict, description="Query parameters"
    )

    # 框架附加元数据，例如 starter_ui_request 等协议标记
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Additional framework-specific metadata"
    )

    def get_cookie(self, name: str, default: Optional[str] = None) -> Optional[str]:
        """Get cookie value by name.

        Args:
            name: Cookie name
            default: Default value if cookie not found

        Returns:
            Cookie value or default

        读取指定 Cookie；缺省时返回 default。是 UserResolver 提取身份最常用的便捷方法。
        """
        return self.cookies.get(name, default)

    def get_header(self, name: str, default: Optional[str] = None) -> Optional[str]:
        """Get header value by name (case-insensitive).

        Args:
            name: Header name
            default: Default value if header not found

        Returns:
            Header value or default

        按大小写不敏感方式读取请求头（HTTP 头字段名本身不区分大小写），
        避免因客户端大小写写法不同导致鉴权头（如 Authorization）读取失败。
        """
        # Case-insensitive header lookup
        name_lower = name.lower()
        for key, value in self.headers.items():
            if key.lower() == name_lower:
                return value
        return default
