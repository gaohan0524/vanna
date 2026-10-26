"""工具参数的公共约束。

目标：把“模型能填什么”收敛到可枚举、可校验的范围，避免自由发挥。

约定：
- 所有工具参数继承 ``ToolArgs``；
- 未知字段直接拒绝（``extra="forbid"``），防止模型塞入不存在的参数；
- 字符串先 strip 再校验长度，避免 ``"  "`` 绕过必填；
- 表名只允许字母/数字/下划线/点，真正的白名单仍在运行时查 catalog。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

# 表名格式：orders、shop.orders、order_items；不允许分号/空格/引号。
TABLE_PATTERN = r"^[A-Za-z_][A-Za-z0-9_.]*$"


class ToolArgs(BaseModel):
    """所有工具参数的基类。"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )
