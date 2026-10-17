"""
Summary-buffer conversation filter (hybrid context window compression).

本模块提供内置的「总结压缩 + 滑动窗口」混合过滤器：保留最近若干 token 的原始消息，
把更早的消息折叠成一份滚动摘要，并以一条消息置顶。这是主流方案（LangChain 的
ConversationSummaryBufferMemory、Claude Code 的「压缩 compaction」、MemGPT/Letta 的
档案记忆）所采用的形态——既保留近期细节，又不会因历史过长撑爆 token 窗口。
"""

from typing import Any, Dict, List, Optional, Tuple

from ..llm import LlmMessage, LlmRequest, LlmService
from ..storage import Message
from ..user import User
from .base import ConversationFilter
from .token_utils import TokenCounter, compute_window_start

DEFAULT_SUMMARY_SYSTEM_PROMPT = (
    "你是会话压缩助手。请把给定的对话历史压缩成一份简洁但信息完整的摘要，"
    "用于在后续对话中替代被裁剪掉的原始消息。"
    "必须保留：用户的查询意图、已识别出的数据库表/字段、已生成的 SQL、"
    "SQL 的执行结果或报错、以及任何尚未解决的事项。用要点列出，控制在 500 字以内。"
)


class SummaryBufferFilter(ConversationFilter):
    """Hybrid context window management: summarize old messages + keep recent window.

    Args:
        llm_service: LLM service used to produce the rolling summary.
        max_tokens: Token budget for the verbatim (recent) window (default 4000).
        tokenizer: Optional exact tokenizer.
        summary_system_prompt: System prompt guiding the summarization LLM call.
        summary_prefix: Prefix prepended to the injected summary message.
        summary_role: Role used for the injected summary message (default "user").
        fallback_on_error: If True, fall back to a naive truncated summary when the
            summarization LLM call fails, instead of raising.

    Example:
        agent = Agent(
            ...,
            conversation_filters=[SummaryBufferFilter(llm_service=llm, max_tokens=4000)],
        )
    """

    def __init__(
        self,
        llm_service: LlmService,
        max_tokens: int = 4000,
        tokenizer: Optional[TokenCounter] = None,
        summary_system_prompt: Optional[str] = None,
        summary_prefix: str = "[以下为之前对话的摘要，请结合它回答]\n",
        summary_role: str = "user",
        fallback_on_error: bool = True,
    ) -> None:
        self.llm_service = llm_service
        self.max_tokens = max_tokens
        self.tokenizer = tokenizer
        self.summary_system_prompt = (
            summary_system_prompt or DEFAULT_SUMMARY_SYSTEM_PROMPT
        )
        self.summary_prefix = summary_prefix
        self.summary_role = summary_role
        self.fallback_on_error = fallback_on_error
        # 每个会话的增量摘要状态：conversation_key -> (已摘要消息数, 摘要文本)
        self._state: Dict[Tuple[Any, ...], Tuple[int, str]] = {}

    async def filter_messages(self, messages: List[Message]) -> List[Message]:
        """Compress older messages into a rolling summary and keep the recent window."""
        if not messages:
            return messages

        start = compute_window_start(messages, self.max_tokens, self.tokenizer)
        recent = messages[start:]
        old = messages[:start]

        if not old:
            return recent

        summary = await self._update_summary(messages, old)
        summary_msg = Message(
            role=self.summary_role,
            content=self.summary_prefix + summary,
        )
        return [summary_msg] + recent

    # ------------------------------------------------------------------ internals

    async def _update_summary(
        self, messages: List[Message], old: List[Message]
    ) -> str:
        key = self._conversation_key(messages)
        prev_count, prev_summary = self._state.get(key, (0, ""))

        if prev_summary and len(old) <= prev_count:
            return prev_summary

        # 只把「新被裁剪掉」的消息与既有摘要合并压缩（增量，而非每次重算全部历史）
        new_messages = old[prev_count:] if prev_summary else old

        try:
            new_summary = await self._summarize(new_messages, prev_summary or None)
        except Exception:
            if not self.fallback_on_error:
                raise
            # 兜底：LLM 摘要失败时用朴素截断，避免整条请求因压缩失败而中断
            new_summary = self._naive_summary(old)

        self._state[key] = (len(old), new_summary)
        self._maybe_trim_state()
        return new_summary

    async def _summarize(
        self, messages: List[Message], existing_summary: Optional[str]
    ) -> str:
        transcript = self._render(messages)

        if existing_summary:
            user_content = (
                f"已有的会话摘要：\n{existing_summary}\n\n"
                f"需要合并进摘要的新对话：\n{transcript}"
            )
        else:
            user_content = f"需要摘要的对话：\n{transcript}"

        request = LlmRequest(
            messages=[LlmMessage(role="user", content=user_content)],
            user=User(id="__summary__"),
            system_prompt=self.summary_system_prompt,
            temperature=0.0,
            max_tokens=1000,
        )
        response = await self.llm_service.send_request(request)
        summary = (response.content or "").strip()
        return summary or self._naive_summary(messages)

    @staticmethod
    def _render(messages: List[Message]) -> str:
        lines = []
        for m in messages:
            role = m.role
            content = m.content or ""
            if m.tool_calls:
                names = ", ".join(tc.name for tc in m.tool_calls)
                content = f"{content} [调用工具: {names}]".strip()
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    @staticmethod
    def _naive_summary(messages: List[Message]) -> str:
        text = SummaryBufferFilter._render(messages)
        if len(text) <= 1000:
            return text
        return text[:1000] + "…(截断)"

    @staticmethod
    def _conversation_key(messages: List[Message]) -> Tuple[Any, ...]:
        first = messages[0]
        return (first.role, first.content, str(first.timestamp))

    def _maybe_trim_state(self) -> None:
        # 防御性清理：避免长跑服务下 state 无限增长（内存可随时重算，直接清空即可）
        if len(self._state) > 1000:
            self._state.clear()
