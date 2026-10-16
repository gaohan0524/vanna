"""
Observability models for spans and metrics.

本模块定义可观测性的两个基础数据结构：Span（追踪单元）与 Metric（指标测量）。
它们是 agent.py 中大量埋点调用的基础——Agent 在每一步（用户解析、LLM 调用、工具
执行、钩子、中间件）创建 span 并记录耗时，形成对整条 Text2SQL 链路的可观测性。
"""

import time
from typing import Any, Dict, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class Span(BaseModel):
    """Represents a unit of work for distributed tracing.

    一次操作的追踪单元（如一次 LLM 调用、一次工具执行）。start_time 记录开始，
    end() 结束并写入 end_time（幂等，重复调用无副作用），duration_ms 计算耗时。
    """

    # span 唯一标识
    id: str = Field(default_factory=lambda: str(uuid4()), description="Span ID")
    # 操作名（如 "agent.tool.execute"、"llm.request"）
    name: str = Field(description="Span name/operation")
    # 开始时间戳
    start_time: float = Field(default_factory=time.time, description="Start timestamp")
    # 结束时间戳；为 None 表示未结束
    end_time: Optional[float] = Field(default=None, description="End timestamp")
    # 附加属性（如 tool 名、success、error 等）
    attributes: Dict[str, Any] = Field(
        default_factory=dict, description="Span attributes"
    )
    # 父 span ID，用于构建调用链
    parent_id: Optional[str] = Field(default=None, description="Parent span ID")

    def end(self) -> None:
        """Mark span as ended.

        结束 span。内部用 end_time 判空保护，重复调用幂等——这正是 agent.py 中
        except/finally 双重 end_span 不会出错的原因。
        """
        if self.end_time is None:
            self.end_time = time.time()

    def duration_ms(self) -> Optional[float]:
        """Get span duration in milliseconds.

        返回耗时（毫秒）；未结束则返回 None。
        """
        if self.end_time is None:
            return None
        return (self.end_time - self.start_time) * 1000

    def set_attribute(self, key: str, value: Any) -> None:
        """Set a span attribute.

        为 span 追加/更新一个属性，用于记录操作上下文（如 user_id、success）。
        """
        self.attributes[key] = value


class Metric(BaseModel):
    """Represents a metric measurement.

    一次指标测量（如某操作耗时、错误计数、token 用量）。name/unit 描述指标，
    tags 提供多维度标签，供监控系统聚合分析。
    """

    # 指标名（如 "agent.tool.duration"）
    name: str = Field(description="Metric name")
    # 指标值
    value: float = Field(description="Metric value")
    # 单位（如 "ms"、"count"、"tokens"）
    unit: str = Field(default="", description="Unit of measurement")
    # 维度标签（如 {"tool": "run_sql", "success": "true"}）
    tags: Dict[str, str] = Field(default_factory=dict, description="Metric tags")
    # 测量时间
    timestamp: float = Field(default_factory=time.time, description="Measurement time")
