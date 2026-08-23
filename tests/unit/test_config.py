"""Config-level assertions for the curated fun-basket."""

from pathlib import Path

from income_stats.config import load_config


def test_basket_has_no_ultra_cheap_items() -> None:
    config = load_config(Path("config.yaml"))
    prices = [item.price_uah for item in config.fun_summary.items.values()]
    assert min(prices) >= 25  # cheapest trinkets removed
    assert max(prices) >= 20000  # at least one high-tier item present


def test_tags_include_purpose_tags() -> None:
    config = load_config(Path("config.yaml"))
    for tag in ("card", "cash", "rent", "dentistry", "health", "transport"):
        assert tag in config.income.tags
