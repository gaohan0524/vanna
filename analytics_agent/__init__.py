"""Analytics agent: 基于 Vanna 2.0 的 ClickHouse 数据分析智能体。

本包是业务代码的唯一入口，与框架 `src/vanna/` 解耦：
- `config.py`        环境变量与连接配置
- `agent_factory.py` 组装被测 Agent（生产与评测共用）
- `evaluation/`      评测框架（Stage 0）
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
