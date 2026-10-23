"""Monopoly+ banker formulas. Pure functions, no I/O, no game state.

Money is whole pounds. Values and share prices that feed into share maths are
kept exact as Fractions; only actual payments are rounded (half away from zero,
like Excel's ROUND), except dividends, which round down per player.
"""

import math
from collections.abc import Hashable, Iterable, Mapping
from fractions import Fraction

Number = int | Fraction


def round_half_up(x: Number) -> int:
    """Round to the nearest integer, halves away from zero (Excel ROUND)."""
    x = Fraction(x)
    n = math.floor(abs(x) + Fraction(1, 2))
    return n if x >= 0 else -n


# --- Deeds -----------------------------------------------------------------

def mortgage_value(price: int) -> int:
    return round_half_up(Fraction(price, 2))


def unmortgage_cost(price: int) -> int:
    """Mortgage value plus 10% interest (normal Monopoly)."""
    return round_half_up(Fraction(price, 2) * Fraction(11, 10))


def house_sale_refund(house_cost: int) -> int:
    """The bank buys a building back at half its cost."""
    return round_half_up(Fraction(house_cost, 2))


def deed_value(price: int, house_cost: int, houses: int, mortgaged: bool) -> Fraction:
    """Printed price plus buildings at cost (hotel = 5 houses), or the mortgage
    value if mortgaged (a mortgaged deed never has buildings)."""
    if mortgaged:
        return Fraction(price, 2)
    return Fraction(price + houses * house_cost)


def even_build_ok(houses: Iterable[int]) -> bool:
    """Buildings within a colour group may differ by at most one."""
    hs = list(houses)
    return not hs or max(hs) - min(hs) <= 1


# --- Companies ---------------------------------------------------------------

def company_value(cash: int, deed_values: Iterable[Number]) -> Fraction:
    return Fraction(cash) + sum((Fraction(v) for v in deed_values), Fraction(0))


def share_price(value: Number, outstanding: int) -> Fraction:
    return Fraction(value) / outstanding if outstanding > 0 else Fraction(0)


def biggest(values: Mapping[Hashable, Number]) -> list:
    """Keys holding the largest positive value, in mapping order (ties included)."""
    if not values:
        return []
    top = max(values.values())
    if top <= 0:
        return []
    return [k for k, v in values.items() if v == top]


def split_shares(values: Mapping[Hashable, Number], total: int,
                 prefer: Hashable | None = None) -> dict:
    """Split `total` new shares in proportion to each player's value.

    Each player gets round(total × value ÷ total value). The biggest contributor
    absorbs the rounding difference; on a tie for biggest, `prefer` wins if it is
    among the tied, else the first tied player in mapping order.
    """
    grand = sum((Fraction(v) for v in values.values()), Fraction(0))
    if grand <= 0:
        raise ValueError("total contributed value must be positive")
    shares = {k: round_half_up(total * Fraction(v) / grand) for k, v in values.items()}
    tied = biggest(values)
    absorber = prefer if prefer in tied else tied[0]
    shares[absorber] += total - sum(shares.values())
    return shares


def merger_values(holdings_a: Mapping, price_a: Number,
                  holdings_b: Mapping, price_b: Number) -> dict:
    """Value of each player's old shares: A shares × A price + B shares × B price.
    Keys keep first-seen order (A's holders, then B-only holders)."""
    out: dict = {}
    for k in list(holdings_a) + [k for k in holdings_b if k not in holdings_a]:
        v = holdings_a.get(k, 0) * Fraction(price_a) + holdings_b.get(k, 0) * Fraction(price_b)
        if v > 0:
            out[k] = v
    return out


# --- Board meetings ----------------------------------------------------------

def vote_passes(yes_shares: int, outstanding: int, threshold: Number = Fraction(1, 2)) -> bool:
    """A motion passes when the yes votes are more than `threshold` of ALL shares."""
    return outstanding > 0 and Fraction(yes_shares) > Fraction(threshold) * outstanding


def can_call_extra_meeting(shares: int, outstanding: int,
                           threshold: Number = Fraction(1, 4)) -> bool:
    return outstanding > 0 and shares > 0 and Fraction(shares, outstanding) >= Fraction(threshold)


def dividend_split(amount: int, holdings: Mapping[Hashable, int]) -> tuple[dict, int]:
    """Share `amount` by shares held, rounding down per player.
    Returns (payout per player, remainder that stays in the envelope)."""
    outstanding = sum(holdings.values())
    if outstanding <= 0:
        return {}, amount
    payouts = {k: (amount * s) // outstanding for k, s in holdings.items() if s > 0}
    return payouts, amount - sum(payouts.values())


def auction_start_bid(new_shares: int, price: Number, fraction: Number = 1) -> int:
    """Opening bid for a bundle of new shares: bundle × current price × fraction."""
    return round_half_up(new_shares * Fraction(price) * Fraction(fraction))


def go_private_owner(holdings: Mapping[Hashable, int]):
    """The player who owns every share, or None."""
    holders = [k for k, s in holdings.items() if s > 0]
    return holders[0] if len(holders) == 1 else None
