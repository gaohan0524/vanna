"""
Compatibility shims for different Python versions.

This module provides compatibility utilities for features that vary across
Python versions.

本模块提供跨 Python 版本的兼容垫片：统一暴露 StrEnum（3.11+ 原生，更早版本用
str, Enum 组合的最小回退实现），供全框架在定义字符串枚举时使用。
"""

try:
    from enum import StrEnum  # Py 3.11+
except ImportError:  # Py < 3.11
    from enum import Enum

    class StrEnum(str, Enum):  # type: ignore[no-redef]
        """Minimal backport of StrEnum for Python < 3.11.

        最小回退实现：继承 str + Enum，使枚举成员既是字符串又能比较/序列化为值，
        行为与 3.11 的 StrEnum 对齐。
        """

        pass


__all__ = ["StrEnum"]
