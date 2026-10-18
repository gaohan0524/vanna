"""
Observability system for telemetry and monitoring.

This module provides interfaces for collecting metrics, traces, and
monitoring agent behavior.
"""

from .base import ObservabilityProvider
from .composite import CompositeObservabilityProvider
from .models import Span, Metric

__all__ = [
    "ObservabilityProvider",
    "CompositeObservabilityProvider",
    "Span",
    "Metric",
]
