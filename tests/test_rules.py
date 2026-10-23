from fractions import Fraction

import pytest

from app import rules


def test_round_half_up_matches_excel():
    assert rules.round_half_up(Fraction(5, 2)) == 3
    assert rules.round_half_up(Fraction(-5, 2)) == -3
    assert rules.round_half_up(Fraction(249, 100)) == 2
    assert rules.round_half_up(7) == 7


def test_deed_values():
    assert rules.mortgage_value(350) == 175
    assert rules.unmortgage_cost(60) == 33
    assert rules.unmortgage_cost(350) == 193  # 192.5 rounds up
    assert rules.house_sale_refund(150) == 75
    assert rules.deed_value(180, 100, 0, False) == 180
    assert rules.deed_value(180, 100, 5, False) == 680  # hotel counts as 5 houses
    assert rules.deed_value(180, 100, 0, True) == 90


def test_even_build():
    assert rules.even_build_ok([1, 2, 2])
    assert not rules.even_build_ok([0, 2, 1])
    assert rules.even_build_ok([])


def test_workbook_founding_example():
    # Bow St + Marlborough St + £100 (P1) and Vine St (P2) -> £460 / £200 -> 70 / 30 at £6.60
    p1 = 180 + 180 + 100
    p2 = 200
    shares = rules.split_shares({"P1": p1, "P2": p2}, 100)
    assert shares == {"P1": 70, "P2": 30}
    value = rules.company_value(100, [180, 180, 200])
    assert value == 660
    assert rules.share_price(value, 100) == Fraction(66, 10)


def test_workbook_merger_example():
    # Orange £900 / 100 shares (P1 40, P2 60) + Red £1,500 / 100 shares (P2 30, P3 70)
    price_a = rules.share_price(900, 100)
    price_b = rules.share_price(1500, 100)
    values = rules.merger_values({"P1": 40, "P2": 60}, price_a, {"P2": 30, "P3": 70}, price_b)
    assert sum(values.values()) == 2400
    shares = rules.split_shares(values, 100)
    assert shares == {"P1": 15, "P2": 41, "P3": 44}
    assert rules.share_price(2400, 100) == 24
    assert rules.biggest(values) == ["P3"]  # P3 becomes CEO


def test_split_rounding_goes_to_biggest():
    # 1/3 each -> 33 + 33 + 33 = 99; the tie is broken by `prefer`
    shares = rules.split_shares({"a": 100, "b": 100, "c": 100}, 100, prefer="b")
    assert shares == {"a": 33, "b": 34, "c": 33}
    # Without a preference, the first tied player absorbs it
    assert rules.split_shares({"a": 100, "b": 100, "c": 100}, 100)["a"] == 34
    # Rounding up overshoots: 2 × 16.5 -> 17 + 17 + 67 = 101, biggest gives one back
    shares = rules.split_shares({"a": 33, "b": 33, "c": 134}, 100)
    assert sum(shares.values()) == 100
    assert shares == {"a": 17, "b": 17, "c": 66}


def test_split_rejects_zero_total():
    with pytest.raises(ValueError):
        rules.split_shares({"a": 0}, 100)


def test_vote_needs_more_than_half_of_all_shares():
    assert not rules.vote_passes(50, 100)
    assert rules.vote_passes(51, 100)
    assert not rules.vote_passes(0, 0)
    assert rules.vote_passes(61, 120, Fraction(1, 2))


def test_extra_meeting_threshold_is_inclusive():
    assert rules.can_call_extra_meeting(25, 100)
    assert not rules.can_call_extra_meeting(24, 100)
    assert rules.can_call_extra_meeting(30, 120)


def test_dividend_rounds_down_and_keeps_remainder():
    payouts, remainder = rules.dividend_split(100, {"a": 70, "b": 30})
    assert payouts == {"a": 70, "b": 30} and remainder == 0
    payouts, remainder = rules.dividend_split(100, {"a": 33, "b": 33, "c": 34})
    assert payouts == {"a": 33, "b": 33, "c": 34} and remainder == 0
    payouts, remainder = rules.dividend_split(50, {"a": 1, "b": 1, "c": 1})
    assert payouts == {"a": 16, "b": 16, "c": 16} and remainder == 2


def test_auction_start_bid():
    assert rules.auction_start_bid(10, Fraction(66, 10)) == 66
    assert rules.auction_start_bid(3, Fraction(2, 3) + Fraction(1, 6)) == 3  # 2.5 -> 3
    assert rules.auction_start_bid(10, 24, Fraction(1, 2)) == 120


def test_go_private_owner():
    assert rules.go_private_owner({"a": 100, "b": 0}) == "a"
    assert rules.go_private_owner({"a": 60, "b": 40}) is None
    assert rules.go_private_owner({}) is None
