"""No-memory web comparison entry smoke tests."""

from __future__ import annotations

from analytics_agent.config import Settings
from analytics_agent.web_app_without_memory import create_app


def test_without_memory_app_has_no_memory_routes():
    app = create_app(Settings(memory_backend="demo"))
    paths = {getattr(route, "path", "") for route in app.routes}

    assert "/memory" not in paths
    assert "/api/memory" not in paths
    assert "/health" in paths
