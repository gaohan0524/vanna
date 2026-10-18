# Vanna 优化记录（P0 / P1）

本目录按「一个问题一份文档」记录本轮 P0/P1 优化：问题背景、主流方案参考、设计决策、代码变动、使用方式与验证结果。

## 索引

| 文档 | 对应问题 | 提交 | 优先级 | 状态 |
|---|---|---|---|---|
| [P0-01-async-llm-sdk.md](./P0-01-async-llm-sdk.md) | #1 LLM 集成用同步阻塞 SDK | `23234c7` | P0 | ✅ |
| [P0-02-stream-finish-reason-usage.md](./P0-02-stream-finish-reason-usage.md) | #2 流式丢失 finish_reason/usage | `23234c7` | P0 | ✅ |
| [P0-03-conversation-isolation.md](./P0-03-conversation-isolation.md) | 跨用户会话越权与路径穿越 | `99fd483` | P0 | ✅ |
| [P0-04-shell-tool-sandbox.md](./P0-04-shell-tool-sandbox.md) | shell 工具命令注入 | `45f5003` | P0 | ✅ |
| [P0-05-nonblocking-sql-runners.md](./P0-05-nonblocking-sql-runners.md) | 同步 DB 驱动阻塞事件循环 | `b573980` | P0 | ✅ |
| [P1-03-parallel-tool-execution.md](./P1-03-parallel-tool-execution.md) | #3 工具串行执行 | `32a1f23` | P1 | ✅ |
| [P1-04-token-level-streaming.md](./P1-04-token-level-streaming.md) | #4 token 级流式未实现 | `fca7ceb` | P1 | ✅ |
| [P1-05-persistence-multi-tenant.md](./P1-05-persistence-multi-tenant.md) | #5 会话/记忆默认内存态 | `c13633d` | P1 | ✅ |
| [P1-06-sql-execution-guardrails.md](./P1-06-sql-execution-guardrails.md) | #6 SQL 执行缺少护栏 | `56628d3` | P1 | ✅ |
| [P1-07-token-cancellation.md](./P1-07-token-cancellation.md) | #7 无 token 级中断/取消 | `6d4650f` | P1 | ✅ |
| [P1-08-context-window-management.md](./P1-08-context-window-management.md) | #8 无内置上下文窗口管理 | `23b5432` | P1 | ✅ |
| [P1-10-structured-output-sql-validation.md](./P1-10-structured-output-sql-validation.md) | #10 无结构化输出约束 | `c0a269a` | P1 | ✅ |
| [P1-11-observability-standards.md](./P1-11-observability-standards.md) | #11 observability 未接标准 | `23e0aa7` | P1 | ✅ |
| [P1-12-agent-mutable-defaults.md](./P1-12-agent-mutable-defaults.md) | #12 Agent 共享可变默认参数 | `f0dadfd` | P1 | ✅ |
| [P1-13-nonblocking-local-io.md](./P1-13-nonblocking-local-io.md) | #13 同步文件 I/O 阻塞事件循环 | `2e8ee0c` | P1 | ✅ |
| [P1-14-uifeatures-mutable-defaults.md](./P1-14-uifeatures-mutable-defaults.md) | #14 UiFeatures 默认分组浅拷贝共享 | `6ecca2b` | P1 | ✅ |
| [P1-15-test-collection-health.md](./P1-15-test-collection-health.md) | #15 test_agent_top_artist 被误收集 | `cacb98b` | P1 | ✅ |
| [P1-16-tool-limit-summary.md](./P1-16-tool-limit-summary.md) | #16 max_tool_iterations 触顶无总结 | `0b1df4d` | P1 | ✅ |
| [P1-17-evaluation-trajectory-capture.md](./P1-17-evaluation-trajectory-capture.md) | #17 评测轨迹 tool_calls/llm_requests 恒为空 | `bfe64e8` | P1 | ✅ |
| [P1-18-tool-error-recovery.md](./P1-18-tool-error-recovery.md) | #18 handle_tool_error 未接线 | `045d8aa` | P1 | ✅ |
| [P1-19-core-errors-semantics.md](./P1-19-core-errors-semantics.md) | #19 core/errors.py 去留与异常语义 | `03242de` | P1 | ✅ |
| [P1-20-thinking-indicator.md](./P1-20-thinking-indicator.md) | #20 include_thinking_indicators 死开关 | `03242de` | P1 | ✅ |

## 未完成项

| 问题 | 状态 | 说明 |
|---|---|---|
| #9 无 human-in-the-loop / 危险操作审批 | 未实现 | 不在本轮范围；可参考 LangGraph `interrupt` / Claude Code approval 设计审批闸门 |

## 文档结构约定

每份文档统一包含：

1. 问题背景
2. 主流方案参考
3. 方案设计
4. 代码变动清单（新增/修改文件 + 关键代码）
5. 使用方式
6. 验证
7. 兼容性与注意事项
8. 后续可扩展点
9. 附：提交与文件
