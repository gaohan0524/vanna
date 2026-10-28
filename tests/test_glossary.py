"""Unit tests for L2 glossary loading and rendering."""

from __future__ import annotations

import pytest

from analytics_agent.context.glossary import (
    glossary_tokens,
    load_glossary,
    render_glossary,
)


def test_default_glossary_has_nine_metrics_and_five_terms():
    glossary = load_glossary()

    assert len(glossary["metrics"]) == 9
    assert len(glossary["terms"]) == 5


def test_render_contains_key_metrics_and_terms():
    text = render_glossary(load_glossary())

    for token in ("GMV", "净收入", "有效订单", "首触渠道", "退款分摊"):
        assert token in text


def test_glossary_budget():
    assert glossary_tokens(load_glossary()) <= 300


def test_conversion_definition_excludes_test_and_soft_delete():
    metric = load_glossary()["metrics"]["conversion_rate"]

    assert "有效会话" in metric["short"]
    assert "deleted_at" in metric["formula"]
    assert "百分比" in metric["pitfalls"]


def test_repurchase_definition_uses_cny_orders():
    metric = load_glossary()["metrics"]["repurchase_rate"]

    assert "CNY" in metric["short"]
    assert "CNY" in metric["formula"]
    assert "排除注销" in metric["pitfalls"]


def test_retention_definition_uses_natural_days():
    metric = load_glossary()["metrics"]["retention_d7"]

    assert "D+1~D+7" in metric["short"]
    assert "< addDays(toDate(signup_at), 8)" in metric["formula"]
    assert "含 D+7" in metric["pitfalls"]


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="glossary not found"):
        load_glossary(tmp_path / "missing.yaml")


def test_invalid_structure_raises(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("metrics: []\n", encoding="utf-8")

    with pytest.raises(ValueError, match="metrics"):
        load_glossary(path)


def test_render_truncates_when_over_budget():
    text = render_glossary(load_glossary(), max_chars=50)

    assert "已截断" in text
