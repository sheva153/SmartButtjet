"""Deterministic income parsing around protected numeric spans."""

import re
from bisect import bisect_left
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext

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
KNOWN_CURRENCY_TYPOS = {
    "жвро": "EUR",
    "евро": "EUR",
    "euroo": "EUR",
    "доллар": "USD",
    "доллари": "USD",
    "долларів": "USD",
    "гривен": "UAH",
}
ALPHABETIC_CURRENCY_TOKEN = "|".join(
    sorted(
        (re.escape(alias) for alias in CURRENCY_ALIASES if alias.isalpha()),
        key=len,
        reverse=True,
    )
)
SYMBOL_CURRENCY_TOKEN = "|".join(
    re.escape(alias) for alias in CURRENCY_ALIASES if not alias.isalpha()
)
PREFIX_CURRENCY_TOKEN = (
    rf"(?:{SYMBOL_CURRENCY_TOKEN}|"
    rf"(?<!\w)(?:{ALPHABETIC_CURRENCY_TOKEN})(?![^\W\d_]))"
)
SUFFIX_CURRENCY_TOKEN = (
    rf"(?:{SYMBOL_CURRENCY_TOKEN}|"
    rf"(?<![^\W\d_])(?:{ALPHABETIC_CURRENCY_TOKEN})(?!\w))"
)
CURRENCY_SEPARATOR = r"[ \t\u00a0]*(?:\r?\n[ \t\u00a0]*)?"
AMOUNT_TOKEN = r"(?:\d{1,3}(?:[ \t\u00a0]+\d{3})+|\d+)(?:[.,]\d{1,2})?"
MONEY_PATTERN = re.compile(
    r"(?<![\w\d.,:/-])"
    rf"(?:(?P<prefix>{PREFIX_CURRENCY_TOKEN}){CURRENCY_SEPARATOR})?"
    rf"(?P<amount>{AMOUNT_TOKEN})"
    rf"(?:{CURRENCY_SEPARATOR}(?P<suffix>{SUFFIX_CURRENCY_TOKEN}))?"
    r"(?!\w)(?![.,:/-]\d)",
    re.IGNORECASE,
)

TIME_PATTERN = re.compile(r"(?<![\d:])(?:[01]?\d|2[0-3]):[0-5]\d(?![\d:])")
UKRAINIAN_PHONE_PATTERN = re.compile(
    r"(?<!\d)(?:\+?38[\s().-]*)?0\d{2}(?:[\s().-]*\d){7}(?!\d)"
)
INTERNATIONAL_PHONE_PATTERN = re.compile(r"(?<![\w\d])\+(?:\d[\s().-]*){7,14}\d(?!\d)")
PHONE_CONTEXT_PATTERN = re.compile(
    r"(?ix)(?<!\w)(?:тел(?:ефон)?|моб(?:ільний)?|"
    r"tel(?:ephone)?|phone|mobile)(?!\w)"
    r"\.?\s*:?\s*\(?\d{2,3}\)?(?:[\s.-]*\d){7}(?!\d)"
)
DATE_PATTERN = re.compile(
    r"(?<![\d./-])(?P<day>\d{1,2})(?P<separator>[./-])"
    r"(?P<month>\d{1,2})(?:(?P=separator)(?P<year>\d{4}))?"
    r"(?![\d./-])"
)
RELATIVE_DATE_PATTERN = re.compile(
    r"\b(?P<relative>сьогодні|вчора|today|yesterday)\b",
    re.IGNORECASE,
)
ADDRESS_NUMBER_TOKEN = r"\d+[A-Za-zА-Яа-яІіЇїЄєҐґ]?(?:[/-]\d+)?"
UNIT_ADDRESS_PATTERN = re.compile(
    rf"(?ix)(?<!\w)(?:буд(?:инок)?|кв(?:артира)?|"
    rf"під['’]?їзд|house|apartment)(?!\w)"
    rf"\.?\s*(?:№|\#)?\s*(?P<number>{ADDRESS_NUMBER_TOKEN})"
)
STREET_WORD_TOKEN = r"[^\W\d_]+(?:[-'’][^\W\d_]+)*"
STREET_ADDRESS_CANDIDATE_PATTERN = re.compile(
    rf"(?ix)(?<!\w)(?:вул(?:иця)?|street)(?!\w)\.?\s*"
    rf"(?:(?:№|\#)\s*(?P<marked_number>{ADDRESS_NUMBER_TOKEN})|"
    rf"(?P<street_name>{STREET_WORD_TOKEN}"
    rf"(?:\s+{STREET_WORD_TOKEN}){{0,3}})\s*,?\s*"
    rf"(?:№|\#)?\s*(?P<named_number>{ADDRESS_NUMBER_TOKEN})|"
    rf"(?P<plain_number>{ADDRESS_NUMBER_TOKEN}))"
)
STREET_INCOME_CONTEXT_PATTERN = re.compile(
    r"(?i)(?<!\w)(?:отрим\w*|зароб\w*|прода\w*|продаж\w*|"
    r"оплат\w*|дохід|доход\w*|повернул\w*|подар\w*|"
    r"переказ\w*|earned|received|sold|income|payment|paid|"
    r"gifted|transferred)(?!\w)"
)
IPV4_LIKE_PATTERN = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
ISO_DATE_LIKE_PATTERN = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")
MAX_DESCRIPTION_LENGTH = 1000


@dataclass(frozen=True)
class _ProtectedContext:
    spans: tuple[tuple[int, int], ...]
    starts: tuple[int, ...]
    absolute_dates: tuple[date, ...]
    invalid_dates: tuple[str, ...]
    relative_dates: tuple[str, ...]
    temporal_spans: tuple[tuple[int, int], ...]


def _merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        previous_start, previous_end = merged[-1]
        merged[-1] = (previous_start, max(previous_end, end))
    return merged


def _overlaps(
    candidate: tuple[int, int],
    spans: tuple[tuple[int, int], ...],
    starts: tuple[int, ...],
) -> bool:
    """Check overlap against sorted disjoint spans in logarithmic time."""
    start, end = candidate
    index = bisect_left(starts, end)
    return index > 0 and spans[index - 1][1] > start


def _has_configured_income_alias(
    text: str,
    config: IncomeConfig | None,
) -> bool:
    if config is None:
        return False
    lowered = text.casefold()
    alias_groups = [*config.categories.values(), *config.tags.values()]
    return any(
        re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", lowered)
        for aliases in alias_groups
        for alias in aliases
    )


def _address_spans(
    text: str,
    config: IncomeConfig | None,
) -> list[tuple[int, int]]:
    spans = [match.span() for match in UNIT_ADDRESS_PATTERN.finditer(text)]
    for match in STREET_ADDRESS_CANDIDATE_PATTERN.finditer(text):
        street_name = match.group("street_name") or ""
        if street_name and (
            STREET_INCOME_CONTEXT_PATTERN.search(street_name)
            or _has_configured_income_alias(street_name, config)
        ):
            continue
        spans.append(match.span())
    return spans


def _protected_context(
    text: str,
    current_date: date,
    config: IncomeConfig | None = None,
) -> _ProtectedContext:
    base_patterns = (
        TIME_PATTERN,
        UKRAINIAN_PHONE_PATTERN,
        INTERNATIONAL_PHONE_PATTERN,
        PHONE_CONTEXT_PATTERN,
        IPV4_LIKE_PATTERN,
        ISO_DATE_LIKE_PATTERN,
    )
    base_spans = _merge_spans(
        [
            *(
                match.span()
                for pattern in base_patterns
                for match in pattern.finditer(text)
            ),
            *_address_spans(text, config),
        ]
    )
    base_spans_tuple = tuple(base_spans)
    base_starts = tuple(start for start, _ in base_spans)
    explicit_currency_spans = tuple(
        match.span()
        for match in MONEY_PATTERN.finditer(text)
        if "." in match.group("amount")
        and (
            match.group("prefix")
            or match.group("suffix")
            or _known_nearby_currency(text, match.span())
        )
    )
    explicit_currency_starts = tuple(start for start, _ in explicit_currency_spans)

    date_spans: list[tuple[int, int]] = []
    absolute_dates: list[date] = []
    invalid_dates: list[str] = []
    for match in DATE_PATTERN.finditer(text):
        if _overlaps(match.span(), base_spans_tuple, base_starts):
            continue
        if _overlaps(
            match.span(),
            explicit_currency_spans,
            explicit_currency_starts,
        ):
            continue
        try:
            parsed_date = date(
                int(match.group("year") or current_date.year),
                int(match.group("month")),
                int(match.group("day")),
            )
        except ValueError:
            invalid_dates.append(match.group(0))
            continue
        date_spans.append(match.span())
        absolute_dates.append(parsed_date)

    relative_spans: list[tuple[int, int]] = []
    relative_dates: list[str] = []
    for match in RELATIVE_DATE_PATTERN.finditer(text):
        if _overlaps(match.span(), base_spans_tuple, base_starts):
            continue
        relative_spans.append(match.span())
        relative_dates.append(match.group("relative").casefold())

    protected = tuple(_merge_spans([*base_spans, *date_spans, *relative_spans]))
    return _ProtectedContext(
        spans=protected,
        starts=tuple(start for start, _ in protected),
        absolute_dates=tuple(absolute_dates),
        invalid_dates=tuple(invalid_dates),
        relative_dates=tuple(relative_dates),
        temporal_spans=tuple(
            _merge_spans(
                [
                    *(match.span() for match in TIME_PATTERN.finditer(text)),
                    *date_spans,
                    *relative_spans,
                ]
            )
        ),
    )


def find_protected_spans(
    text: str,
    config: IncomeConfig | None = None,
) -> list[tuple[int, int]]:
    """Return merged source offsets that cannot be interpreted as money."""
    current_date = datetime.now(UTC).date()
    return list(_protected_context(text, current_date, config).spans)


def _decimal_from_text(value: str) -> Decimal | None:
    compact = re.sub(r"[ \t\u00a0]+", "", value).replace(",", ".")
    try:
        with localcontext() as context:
            context.prec = max(
                28, sum(character.isdigit() for character in compact) + 2
            )
            amount = Decimal(compact)
            quantized = amount.quantize(Decimal("0.01"))
    except InvalidOperation:
        return None
    if amount <= 0:
        return None
    return quantized


def _known_nearby_currency(
    text: str,
    span: tuple[int, int],
) -> tuple[str, tuple[int, int]] | None:
    start, end = span
    before = re.search(
        rf"([^\W\d_]+){CURRENCY_SEPARATOR}$",
        text[:start],
        re.UNICODE,
    )
    after = re.match(
        rf"{CURRENCY_SEPARATOR}([^\W\d_]+)",
        text[end:],
        re.UNICODE,
    )
    for nearby, offset in ((after, end), (before, 0)):
        if nearby is None:
            continue
        word = nearby.group(1).casefold()
        if currency := KNOWN_CURRENCY_TYPOS.get(word):
            token_start, token_end = nearby.span(1)
            return currency, (offset + token_start, offset + token_end)
    return None


def _currency_for_match(text: str, match: re.Match[str], default_currency: str) -> str:
    token = (match.group("prefix") or match.group("suffix") or "").casefold()
    if currency := CURRENCY_ALIASES.get(token):
        return currency
    if typo := _known_nearby_currency(text, match.span()):
        return typo[0]
    return default_currency


def _detect_labels(text: str, aliases: dict[str, list[str]]) -> list[str]:
    lowered = text.casefold()
    return [
        label
        for label, terms in aliases.items()
        if any(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", lowered) for term in terms)
    ]


def _income_date(context: _ProtectedContext, current_date: date) -> date:
    if context.absolute_dates:
        return context.absolute_dates[0]
    if context.relative_dates and context.relative_dates[0] in {"вчора", "yesterday"}:
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
    current_date = today or datetime.now(UTC).date()
    context = _protected_context(text, current_date, config)
    if context.invalid_dates:
        raise ValueError(f"Invalid income date: {context.invalid_dates[0]}")
    candidates: list[tuple[re.Match[str], Decimal]] = []
    for match in MONEY_PATTERN.finditer(text):
        if _overlaps(match.span(), context.spans, context.starts):
            continue
        amount = _decimal_from_text(match.group("amount"))
        if amount is not None:
            candidates.append((match, amount))
    if not candidates:
        return []

    categories = _detect_labels(text, config.categories) or ["other"]
    tags = _detect_labels(text, config.tags)
    removed_spans = [match.span() for match, _ in candidates]
    removed_spans.extend(
        typo[1]
        for match, _ in candidates
        if (typo := _known_nearby_currency(text, match.span()))
    )
    removed_spans.extend(context.temporal_spans)
    description = re.sub(
        r"\s+",
        " ",
        _without_spans(text, _merge_spans(removed_spans)),
    ).strip(" ,;:-")[:MAX_DESCRIPTION_LENGTH]
    income_date = _income_date(context, current_date)

    return [
        ParsedIncome(
            amount=amount,
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
        for match, amount in candidates
    ]
