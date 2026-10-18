"""
Strict JSON Schema helpers for structured output / strict tool calling.

主流供应商的「严格结构化输出」（OpenAI Structured Outputs / strict function calling、
Gemini response_schema 等）要求 JSON Schema 满足一组约束，否则请求会被拒绝：

- 每个 object 必须显式声明 ``additionalProperties: false``；
- 所有属性都必须列在 ``required`` 中（即不允许「可选字段」）；
- 不允许 ``default``（因为字段必须由模型给出）。

Pydantic 的 ``model_json_schema()`` 默认不满足这些约束（可选字段、默认值等），
因此这里提供一份递归转换器，把普通 JSON Schema 转成严格模式可用的形态。
"""

from __future__ import annotations

import copy
from typing import Any, Dict


def make_strict_json_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Return a strict-mode compatible copy of a JSON schema.

    Args:
        schema: A JSON schema dict (e.g. from ``pydantic_model.model_json_schema()``).

    Returns:
        A deep-copied schema with ``additionalProperties: false`` on every object,
        all properties marked required, and ``default`` keys removed.

    注意：该转换不改变字段类型与嵌套结构；对 ``anyOf`` / ``$ref`` / ``$defs`` 原样保留。
    """
    strict_schema = copy.deepcopy(schema)
    _strictify(strict_schema)
    return strict_schema


def _strictify(node: Any) -> None:
    if isinstance(node, dict):
        # 移除 default（严格模式下所有字段都必须由模型提供）
        node.pop("default", None)

        properties = node.get("properties")
        if isinstance(properties, dict):
            # object 必须禁止额外属性，且所有属性都必须 required
            node["additionalProperties"] = False
            node["required"] = list(properties.keys())

        for value in node.values():
            _strictify(value)
    elif isinstance(node, list):
        for item in node:
            _strictify(item)
