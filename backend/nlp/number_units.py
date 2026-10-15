# number_units.py — parse the numbers a user types into a threshold:
# "10%", "5 million", "10 lakh", "₹2 crore", "1 cr", "10L".
#
# Pure text -> Decimal. Percent figures stay as typed (10% -> 10); money figures are
# scaled to plain units (10 lakh -> 1000000). Whether those plain units match the
# scale a given return reports in is NOT decided here (see analysis.py notes).

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Optional

_PCT_UNITS = {"%", "percent", "pct", "per cent", "percentage"}

_SCALE: dict[str, Decimal] = {
    "k": Decimal("1e3"), "thousand": Decimal("1e3"),
    "l": Decimal("1e5"), "lakh": Decimal("1e5"), "lakhs": Decimal("1e5"),
    "lac": Decimal("1e5"), "lacs": Decimal("1e5"),
    "m": Decimal("1e6"), "mn": Decimal("1e6"), "million": Decimal("1e6"),
    "cr": Decimal("1e7"), "crore": Decimal("1e7"), "crores": Decimal("1e7"),
    "b": Decimal("1e9"), "bn": Decimal("1e9"), "billion": Decimal("1e9"),
}

_UNIT_RE = (
    r"%|percent(?:age)?|pct|per\s*cent|lakhs?|lacs?|crores?|cr|million|mn|billion|bn|thousand"
    r"|[klmb](?![a-z])"
)


def amount_pattern(sfx: str = "") -> str:
    """Regex source for one amount, with uniquely-suffixed named groups so several
    amounts can live in one compound pattern ("between 10 and 30%")."""
    return (
        rf"(?P<neg{sfx}>-\s*)?(?:₹|rs\.?|inr)?\s*"
        rf"(?P<num{sfx}>\d[\d,]*(?:\.\d+)?)\s*"
        rf"(?P<unit{sfx}>{_UNIT_RE})?"
    )


def build_amount(num: str, unit: Optional[str], neg: Optional[str],
                 inherit_unit: Optional[str] = None) -> Optional[tuple[Decimal, str]]:
    """(value, kind) where kind is "pct" | "abs" | "unknown". None if `num` is unparseable."""
    try:
        value = Decimal(num.replace(",", ""))
    except InvalidOperation:
        return None
    u = re.sub(r"\s+", " ", (unit or inherit_unit or "").lower()).strip()
    if neg:
        value = -value
    if u in _PCT_UNITS:
        return value, "pct"
    if u in _SCALE:
        return value * _SCALE[u], "abs"
    return value, "unknown"


_AMOUNT_RE = re.compile(amount_pattern(), re.IGNORECASE)


def parse_amount(text: str) -> Optional[tuple[Decimal, str]]:
    """The first amount in `text`, or None."""
    m = _AMOUNT_RE.search(text)
    if not m:
        return None
    return build_amount(m.group("num"), m.group("unit"), m.group("neg"))
