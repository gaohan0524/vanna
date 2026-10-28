"""Optional real-model semantic smoke for Stage 3.

Run with:
    RUN_EMBEDDING_SMOKE=1 pytest tests/test_chroma_semantic_smoke.py -q
"""

from __future__ import annotations

import asyncio
import os

import pytest
from vanna import User
from vanna.core.tool import ToolContext

from analytics_agent.memory.backends.chroma_local import ChromaLocalMemory
from analytics_agent.memory.embedding import ChineseEmbeddingFunction

pytestmark = pytest.mark.skipif(
    not os.getenv("RUN_EMBEDDING_SMOKE"),
    reason="set RUN_EMBEDDING_SMOKE=1 to load the Chinese embedding model",
)


def test_three_chinese_seeds_match_their_queries(tmp_path):
    memory = ChromaLocalMemory(
        persist_directory=str(tmp_path),
        collection_name="semantic_smoke",
        embedding_function=ChineseEmbeddingFunction(),
    )
    context = ToolContext(
        user=User(id="smoke", metadata={"tenant_id": "analytics"}),
        conversation_id="smoke",
        request_id="smoke",
        agent_memory=memory,
    )
    seeds = {
        "净收入": "品类净收入 = 订单金额 - approved 退款；item_amount 只用于分摊比例。",
        "复购率": "复购率 = 近 90 天有至少 2 笔 CNY 有效订单的用户占比。",
        "七日留存": "7 日留存 = 注册次日起 D+1~D+7 自然日内有会话的用户占比。",
    }
    queries = {
        "各品类扣掉退款后的收入": "净收入",
        "90 天内买过两次的人占多少": "复购率",
        "注册后一周内还回来的人": "七日留存",
    }

    async def run():
        for tag, text in seeds.items():
            await memory.save_text_memory(
                f"[{tag}] {text}",
                context,
                metadata={"scope": "shared", "source": "semantic-smoke"},
            )
        matches = {}
        for query, expected in queries.items():
            results = await memory.search_text_memories(
                query, context, limit=3, similarity_threshold=0.0
            )
            assert results
            matches[expected] = results[0].memory.content
        return matches

    matches = asyncio.run(run())
    for expected, content in matches.items():
        assert expected in content
