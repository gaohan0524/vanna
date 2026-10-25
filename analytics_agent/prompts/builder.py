"""Assemble the system prompt: base + L0 + L1 schema + L2 glossary."""

from __future__ import annotations

from datetime import date
from typing import Callable, Optional

from vanna.core.system_prompt import DefaultSystemPromptBuilder

from .templates import L0_RULES


class AnalyticsPromptBuilder(DefaultSystemPromptBuilder):
    """Append stable instructions, schema and glossary to the default prompt.

    Args:
        schema_text: rendered L1 schema (from ``context.render.render_schema``).
        glossary_text: rendered L2 metrics/terms (empty in T1-2).
        today: injectable date provider for tests.
    """

    def __init__(
        self,
        schema_text: str,
        glossary_text: str = "",
        *,
        today: Optional[Callable[[], date]] = None,
    ) -> None:
        super().__init__()  # 保留默认角色与工具清单
        self._schema_text = schema_text.strip()
        self._glossary_text = glossary_text.strip()
        self._today = today or date.today

    @property
    def schema_text(self) -> str:
        return self._schema_text

    async def build_system_prompt(self, user, tools) -> str:
        base = await super().build_system_prompt(user, tools) or ""
        parts = [
            base,
            f"今天日期：{self._today().isoformat()}",
            L0_RULES,
            self._schema_text,
        ]
        if self._glossary_text:
            parts.append(self._glossary_text)
        return "\n\n".join(part for part in parts if part)
