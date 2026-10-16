"""
SQL runner capability models.

This module contains data models for SQL execution.

本模块定义 SQL 执行的参数模型：run_sql 工具的入参。
"""

from pydantic import BaseModel, Field


class RunSqlToolArgs(BaseModel):
    """Arguments for run_sql tool.

    run_sql 工具的参数模型：唯一的 sql 字段即 LLM 生成的 SQL 语句。
    该模型会被 ToolRegistry 用作参数校验的 Pydantic schema。
    """

    sql: str = Field(description="SQL query to execute")
