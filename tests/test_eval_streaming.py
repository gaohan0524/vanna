"""Streaming evaluation runner: per-case completion progress."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from vanna.core.evaluation import EvaluationRunner


def test_streaming_yields_each_case_with_progress():
    runner = EvaluationRunner(evaluators=[])
    cases = [SimpleNamespace(id=f"X-{index}") for index in range(3)]
    agent = SimpleNamespace(llm_middlewares=[])

    async def fake_run(_agent, case):
        await asyncio.sleep(0)
        return SimpleNamespace(test_case=case)

    runner._run_single_test_case = fake_run  # type: ignore[method-assign]

    seen = []

    async def run():
        async for result, completed, total in runner.run_evaluation_streaming(
            agent, cases
        ):
            seen.append((result.test_case.id, completed, total))

    asyncio.run(run())

    assert len(seen) == 3
    assert [item[1] for item in seen] == [1, 2, 3]
    assert all(item[2] == 3 for item in seen)
    assert {item[0] for item in seen} == {"X-0", "X-1", "X-2"}
