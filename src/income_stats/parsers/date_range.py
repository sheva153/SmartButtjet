"""Parse a two-date reporting range from user text."""

from __future__ import annotations

import re
from datetime import date

_DATE = re.compile(r"(?<!\d)(\d{1,2})[./](\d{1,2})(?:[./](\d{4}))?(?!\d)")


def parse_date_range(text: str, *, today: date) -> tuple[date, date] | None:
    """Parse the first two DD.MM or DD.MM.YYYY dates in ``text``, sorted.

    Returns ``None`` when fewer than two dates are found or any date is
    invalid (e.g. 31.02).
    """
    matches = _DATE.findall(text)
    if len(matches) < 2:
        return None
    parsed: list[date] = []
    for day, month, year in matches[:2]:
        try:
            parsed.append(date(int(year) if year else today.year, int(month), int(day)))
        except ValueError:
            return None
    start, end = sorted(parsed)
    return start, end
