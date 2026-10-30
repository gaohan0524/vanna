"""Delivery web app smoke tests."""

from __future__ import annotations

import asyncio

from vanna.core.user import RequestContext

from analytics_agent.web_app import (
    WebUserResolver,
    load_index_html,
    load_memory_html,
)


def _user(email: str | None):
    cookies = {"vanna_email": email} if email else {}
    return asyncio.run(WebUserResolver().resolve_user(RequestContext(cookies=cookies)))


def test_web_default_user_is_analyst():
    user = _user(None)

    assert user.group_memberships == ["analyst"]


def test_web_admin_cookie_gets_admin_group():
    user = _user("admin@example.com")

    assert user.group_memberships == ["admin"]


def test_web_regular_cookie_gets_analyst_group():
    user = _user("analyst@example.com")

    assert user.group_memberships == ["analyst"]


def test_business_index_html_is_separate_file():
    html = load_index_html()

    assert "<title>智能体</title>" in html
    assert "plotly-2.35.2.min.js" in html


def test_memory_html_is_available():
    html = load_memory_html()

    assert "Analytics Agent 记忆查看" in html
    assert "/api/memory" in html
