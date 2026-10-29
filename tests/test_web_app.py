"""Delivery web app smoke tests."""

from __future__ import annotations

import asyncio

from vanna.core.user import RequestContext

from analytics_agent.web_app import WebUserResolver


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
