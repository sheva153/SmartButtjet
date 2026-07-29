"""Deterministic income parsing around protected numeric spans."""

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from difflib import get_close_matches

from income_stats.config.settings import IncomeConfig
from income_stats.models.domain import ParsedIncome

CURRENCY_ALIASES = {
    "₴": "UAH",
    "грн": "UAH",
    "гривня": "UAH",
    "гривні": "UAH",
    "гривень": "UAH",
    "uah": "UAH",
    "$": "USD",
    "usd": "USD",
    "дол": "USD",
    "долар": "USD",
    "долари": "USD",
    "доларів": "USD",
    "dollar": "USD",
    "dollars": "USD",
    "€": "EUR",
    "eur": "EUR",
    "євро": "EUR",
    "euro": "EUR",
    "euros": "EUR",
}
FUZZY_CURRENCY_WORDS = {
    alias: currency
    for alias, currency in CURRENCY_ALIASES.items()
    if alias.isalpha() and len(alias) >= 3
}
CURRENCY_TOKEN = "|".join(
    sorted((re.escape(alias) for alias in CURRENCY_ALIASES), key=len, reverse=True)
)
AMOUNT_TOKEN = r"(?:\d{1,3}(?:[ \u00a0]\d{3})+|\d+)(?:[.,]\d{1,2})?"
MONEY_PATTERN = re.compile(
    rf"(?:(?P<prefix>{CURRENCY_TOKEN})\s*)?"
    rf"(?P<amount>{AMOUNT_TOKEN})"
    rf"(?:\s*(?P<suffix>{CURRENCY_TOKEN}))?",
    re.IGNORECASE,
)

TIME_PATTERN = re.compile(r"(?<!\d)(?:[01]?\d|2[0-3]):[0-5]\d(?!\d)")
UKRAINIAN_PHONE_PATTERN = re.compile(
    r"(?<!\d)(?:\+?38[\s().-]*)?0\d{2}(?:[\s().-]*\d){7}(?!\d)"
)
INTERNATIONAL_PHONE_PATTERN = re.compile(r"(?<![\w\d])\+(?:\d[\s().-]*){7,14}\d(?!\d)")
DATE_PATTERN = re.compile(
    r"(?<!\d)(?P<day>\d{1,2})[./-](?P<month>\d{1,2})"
    r"(?:[./-](?P<year>\d{4}))?(?!\d)"
)
RELATIVE_DATE_PATTERN = re.compile(
    r"\b(?P<relative>сьогодні|вчора|today|yesterday)\b",
    re.IGNORECASE,
)
ADDRESS_PATTERN = re.compile(
    r"(?ix)\b(?:вул(?:иця)?|буд(?:инок)?|кв(?:артира)?|"
    r"під['’]?їзд|street|house|apartment)\.?\s*"
    r"(?:[^\W\d_.'’]+[.'’]?\s*,?\s*){0,4}"
    r"(?P<number>\d+[A-Za-zА-Яа-яІіЇїЄєҐґ]?(?:[/-]\d+)?)"
)


def _merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        previous_start, previous_end = merged[-1]
        merged[-1] = (previous_start, max(previous_end, end))
    return merged


def find_protected_spans(text: str) -> list[tuple[int, int]]:
    """Return merged source offsets that cannot be interpreted as money."""
    patterns = (
        TIME_PATTERN,
        UKRAINIAN_PHONE_PATTERN,
        INTERNATIONAL_PHONE_PATTERN,
        DATE_PATTERN,
        RELATIVE_DATE_PATTERN,
        ADDRESS_PATTERN,
    )
    spans = [match.span() for pattern in patterns for match in pattern.finditer(text)]
    return _merge_spans(spans)


def _overlaps(
    candidate: tuple[int, int], protected_spans: list[tuple[int, int]]
) -> bool:
    start, end = candidate
    return any(
        start < protected_end and protected_start < end
        for protected_start, protected_end in protected_spans
    )


def _decimal_from_text(value: str) -> Decimal:
    compact = value.replace(" ", "").replace("\u00a0", "").replace(",", ".")
    try:
        amount = Decimal(compact)
    except InvalidOperation as error:
        raise ValueError(f"Invalid amount: {value}") from error
    if amount <= 0:
        raise ValueError("Amount must be positive")
    return amount.quantize(Decimal("0.01"))


def _fuzzy_nearby_currency(text: str, span: tuple[int, int]) -> str | None:
    start, end = span
    before = re.search(r"([^\W\d_]{3,12})\s*$", text[:start], re.UNICODE)
    after = re.match(r"\s*([^\W\d_]{3,12})", text[end:], re.UNICODE)
    for nearby in (after, before):
        if nearby is None:
            continue
        word = nearby.group(1).casefold()
        match = get_close_matches(word, FUZZY_CURRENCY_WORDS, n=1, cutoff=0.68)
        if match:
            return FUZZY_CURRENCY_WORDS[match[0]]
    return None


def _currency_for_match(text: str, match: re.Match[str], default_currency: str) -> str:
    token = (match.group("prefix") or match.group("suffix") or "").casefold()
    return (
        CURRENCY_ALIASES.get(token)
        or _fuzzy_nearby_currency(text, match.span())
        or default_currency
    )


def _detect_labels(text: str, aliases: dict[str, list[str]]) -> list[str]:
    lowered = text.casefold()
    return [
        label
        for label, terms in aliases.items()
        if any(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", lowered) for term in terms)
    ]


def _income_date(text: str, current_date: date) -> date:
    absolute = DATE_PATTERN.search(text)
    if absolute:
        year = int(absolute.group("year") or current_date.year)
        try:
            return date(
                year,
                int(absolute.group("month")),
                int(absolute.group("day")),
            )
        except ValueError as error:
            raise ValueError(f"Invalid income date: {absolute.group(0)}") from error

    relative = RELATIVE_DATE_PATTERN.search(text)
    if relative and relative.group("relative").casefold() in {"вчора", "yesterday"}:
        return current_date - timedelta(days=1)
    return current_date


def _without_spans(text: str, spans: list[tuple[int, int]]) -> str:
    characters = list(text)
    for start, end in spans:
        characters[start:end] = " " * (end - start)
    return "".join(characters)


def parse_income_message(
    text: str,
    config: IncomeConfig,
    *,
    today: date | None = None,
) -> list[ParsedIncome]:
    """Parse every unprotected amount in a message as income."""
    protected_spans = find_protected_spans(text)
    matches = [
        match
        for match in MONEY_PATTERN.finditer(text)
        if not _overlaps(match.span(), protected_spans)
    ]
    if not matches:
        return []

    current_date = today or datetime.now(UTC).date()
    categories = _detect_labels(text, config.categories) or ["other"]
    tags = _detect_labels(text, config.tags)
    removed_spans = [match.span() for match in matches]
    removed_spans.extend(match.span() for match in DATE_PATTERN.finditer(text))
    removed_spans.extend(match.span() for match in RELATIVE_DATE_PATTERN.finditer(text))
    removed_spans.extend(match.span() for match in TIME_PATTERN.finditer(text))
    description = re.sub(
        r"\s+",
        " ",
        _without_spans(text, _merge_spans(removed_spans)),
    ).strip(" ,;:-")
    income_date = _income_date(text, current_date)

    return [
        ParsedIncome(
            amount=_decimal_from_text(match.group("amount")),
            currency=_currency_for_match(
                text,
                match,
                config.default_currency,
            ),
            categories=categories,
            tags=tags,
            description=description,
            income_date=income_date,
        )
        for match in matches
    ]
