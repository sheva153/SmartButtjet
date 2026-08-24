"""Config-level assertions for the curated fun-basket."""

from pathlib import Path

from income_stats.config import load_config


def test_basket_has_no_ultra_cheap_items() -> None:
    config = load_config(Path("config.yaml"))
    prices = [item.price_uah for item in config.fun_summary.items.values()]
    assert min(prices) >= 25  # cheapest trinkets removed
    assert max(prices) >= 20000  # at least one high-tier item present


def test_basket_has_luxury_tier() -> None:
    config = load_config(Path("config.yaml"))
    assert any(item.luxury for item in config.fun_summary.items.values())
    assert max(i.price_uah for i in config.fun_summary.items.values()) >= 100000


def test_tags_include_purpose_tags() -> None:
    config = load_config(Path("config.yaml"))
    for tag in ("card", "cash", "rent", "dentistry", "health", "transport"):
        assert tag in config.income.tags


def test_forecast_method_and_phrase_tiers_configured() -> None:
    config = load_config(Path("config.yaml"))
    assert config.analytics.forecast_method == "weighted"
    assert len(config.goals.behind_phrases) >= 2
    assert len(config.goals.ahead_phrases) >= 2


def test_fx_and_tags_configured() -> None:
    config = load_config(Path("config.yaml"))
    assert config.analytics.fx_to_uah["USD"] == 41.0
    assert config.analytics.fx_to_uah["EUR"] == 45.0
    assert str(config.storage.tags_file).endswith("tags.csv")
