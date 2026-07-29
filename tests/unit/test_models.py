from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from income_stats.config.settings import AppConfig, load_config
from income_stats.models.domain import IncomeRecord, normalize_label


def test_record_normalizes_multiple_categories_and_tags() -> None:
    record = IncomeRecord(
        telegram_message_id=1,
        chat_id=-100,
        user_id=7,
        original_text="зп 500 на картку",
        amount=Decimal("500"),
        currency="uah",
        categories=["Salary", "salary", "Debt"],
        tags=["Card", "card"],
        updated_by=7,
    )

    assert record.currency == "UAH"
    assert record.categories == ["salary", "debt"]
    assert record.tags == ["card"]


def test_record_uses_other_when_categories_are_empty() -> None:
    record = IncomeRecord(
        telegram_message_id=1,
        chat_id=-100,
        user_id=7,
        original_text="500",
        amount=Decimal("500"),
        currency="UAH",
        categories=[],
        updated_by=7,
    )

    assert record.categories == ["other"]


def test_config_accepts_alias_maps() -> None:
    config = AppConfig.model_validate(
        {
            "income": {
                "categories": {"Salary": ["Зарплата", "зп"], "other": []},
                "tags": {"Card": ["Картка"]},
            }
        }
    )

    assert config.income.categories["salary"] == ["зарплата", "зп"]
    assert config.income.tags["card"] == ["картка"]


def test_load_config_reads_taxonomy(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "income:\n"
        "  categories:\n"
        "    Salary: [Зарплата, зп]\n"
        "    other: []\n"
        "  tags:\n"
        "    Card: [Картка]\n",
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config.income.categories == {
        "salary": ["зарплата", "зп"],
        "other": [],
    }
    assert config.income.tags == {"card": ["картка"]}


def test_normalize_label_rejects_blank_values() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        normalize_label("  ")


def test_record_rejects_non_positive_amount() -> None:
    with pytest.raises(ValidationError):
        IncomeRecord(
            telegram_message_id=1,
            chat_id=-100,
            user_id=7,
            original_text="0",
            amount=Decimal("0"),
            currency="UAH",
            updated_by=7,
        )
