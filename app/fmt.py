"""Display formatting shared by event descriptions and templates."""

from fractions import Fraction

CURRENCY = "£"


def money(x) -> str:
    """Whole pounds as £1,234; fractional amounts with pence."""
    x = Fraction(x)
    sign = "−" if x < 0 else ""
    x = abs(x)
    if x.denominator == 1:
        return f"{sign}{CURRENCY}{int(x):,}"
    return f"{sign}{CURRENCY}{float(x):,.2f}"


def price(x) -> str:
    """Share prices always show pence."""
    return f"{CURRENCY}{float(Fraction(x)):,.2f}"


def pct(x) -> str:
    x = float(Fraction(x)) * 100
    return f"{x:.0f}%" if abs(x - round(x)) < 1e-9 else f"{x:.1f}%"


def names(items) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " & " + items[-1]
