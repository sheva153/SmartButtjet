import subprocess
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from income_stats.config.settings import AppConfig, IncomeConfig, load_config
from income_stats.models.domain import (
    IncomeRecord,
    ParsedIncome,
    normalize_label,
)


def test_record_normalizes_multiple_categories_and_tags() -> None:
    record = IncomeRecord(
        telegram_message_id=1,
        chat_id=-100,
        user_id=7,
        original_text="зп 500 на картку",
        amount=Decimal("500"),
        currency=" uah ",
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


@pytest.mark.parametrize(
    "categories",
    [
        pytest.param(
            {"other": [""], "salary": ["зп"]},
            id="blank-alias",
        ),
        pytest.param(
            {"Salary": ["зп"], " salary ": ["зарплата"], "other": []},
            id="normalized-key-collision",
        ),
        pytest.param({}, id="empty-categories"),
        pytest.param({"salary": ["зп"]}, id="missing-other-fallback"),
    ],
)
def test_config_rejects_invalid_taxonomy(
    categories: dict[str, list[str]],
) -> None:
    with pytest.raises(ValidationError):
        IncomeConfig(categories=categories)


@pytest.mark.parametrize("currency", ["", "UA", "EURO", "U4H", "грн"])
def test_config_rejects_invalid_default_currency(currency: str) -> None:
    with pytest.raises(ValidationError):
        IncomeConfig(default_currency=currency)


def test_config_normalizes_default_currency_before_length_validation() -> None:
    assert IncomeConfig(default_currency=" uah ").default_currency == "UAH"


def test_parsed_income_normalizes_currency_before_length_validation() -> None:
    parsed = ParsedIncome(
        amount=Decimal("1"),
        currency=" uah ",
        income_date=date(2026, 7, 29),
    )

    assert parsed.currency == "UAH"


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


def test_checked_in_config_is_accepted_by_legacy_entrypoint() -> None:
    repository_root = Path(__file__).parents[2]
    result = subprocess.run(
        [sys.executable, "main.py", "check-config"],
        check=False,
        capture_output=True,
        cwd=repository_root,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Configuration is valid."


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


@pytest.mark.parametrize("amount", [Decimal("0"), Decimal("-1")])
def test_parsed_income_rejects_non_positive_amount(amount: Decimal) -> None:
    with pytest.raises(ValidationError):
        ParsedIncome(
            amount=amount,
            currency="UAH",
            income_date=date(2026, 7, 29),
        )


@pytest.mark.parametrize("currency", ["", "UA", "EURO", "U4H", "грн"])
def test_models_reject_invalid_currency(currency: str) -> None:
    with pytest.raises(ValidationError):
        ParsedIncome(
            amount=Decimal("1"),
            currency=currency,
            income_date=date(2026, 7, 29),
        )
