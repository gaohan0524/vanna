"""Static context assets for the analytics agent (schema catalog, glossary).

注意：这里刻意不 re-export 子模块，避免 `python -m analytics_agent.context.schema_catalog`
触发“package 先导入、模块再执行”的 RuntimeWarning。
"""
