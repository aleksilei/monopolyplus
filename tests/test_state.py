from fractions import Fraction

import pytest

from app.state import RuleError
from conftest import own


def test_workbook_founding(store):
    own(store, "p1", "bow_street", "marlborough_street")
    own(store, "p2", "vine_street")
    state, _ = store.record(("found", {"company": "c1", "group": "ORANGE", "name": "",
                                       "ceo": "p1", "cash": {"p1": 100}}))
    c = state.companies["c1"]
    assert c.name == "Orange Inc."  # default from the board data
    assert c.holdings == {"p1": 70, "p2": 30}
    assert c.cash == 100 and c.counted_round == 1
    assert state.company_value(c) == 660
    assert state.share_price(c) == Fraction(66, 10)
    assert all(d.owner == "c1" for d in state.group_deeds("ORANGE"))


def test_founding_needs_two_owners_and_the_whole_set(store):
    own(store, "p1", "bow_street", "marlborough_street")
    with pytest.raises(RuleError, match="bank still holds"):
        store.record(("found", {"company": "c1", "group": "ORANGE", "ceo": "p1", "cash": {}}))
    own(store, "p1", "vine_street")
    with pytest.raises(RuleError, match="one player owns"):
        store.record(("found", {"company": "c1", "group": "ORANGE", "ceo": "p1", "cash": {}}))
    own(store, "p2", "vine_street")
    with pytest.raises(RuleError, match="CEO must be one of the founders"):
        store.record(("found", {"company": "c1", "group": "ORANGE", "ceo": "p3", "cash": {}}))


def test_companies_keep_their_deeds(orange_red):
    with pytest.raises(RuleError, match="belongs to Orange Inc."):
        orange_red.record(("set_owner", {"deed": "bow_street", "owner": "p3"}))


def test_workbook_merger(orange_red):
    state = orange_red.state()
    assert state.company_value(state.companies["c1"]) == 900
    assert state.company_value(state.companies["c2"]) == 1500
    state, _ = orange_red.record(("merge", {
        "company": "c3", "a": "c1", "b": "c2", "name": "Orange-Red Holdings",
        "yes_a": ["p2"], "yes_b": ["p3"]}))
    new = state.companies["c3"]
    assert new.holdings == {"p1": 15, "p2": 41, "p3": 44}
    assert new.ceo == "p3"
    assert state.company_value(new) == 2400 and state.share_price(new) == 24
    assert state.companies["c1"].status == state.companies["c2"].status == "merged"
    assert {d.owner for d in state.group_deeds("ORANGE") + state.group_deeds("RED")} == {"c3"}


def test_merger_needs_both_boards(orange_red):
    with pytest.raises(RuleError, match="motion fails at Red"):
        orange_red.record(("merge", {"company": "c3", "a": "c1", "b": "c2", "name": "X",
                                     "yes_a": ["p2"], "yes_b": ["p2"]}))


def test_dividend_vote_and_payout(orange_red):
    with pytest.raises(RuleError, match="40 of 100 shares voted yes"):
        orange_red.record(("dividend", {"company": "c1", "amount": 100, "yes": ["p1"]}))
    with pytest.raises(RuleError, match="Count the envelope"):
        orange_red.record(("dividend", {"company": "c1", "amount": 1000, "yes": ["p2"]}))
    state, [entry] = orange_red.record(("dividend", {"company": "c1", "amount": 99, "yes": ["p2"]}))
    assert state.companies["c1"].cash == 340 - 39 - 59  # £1 remainder stays
    assert "£1 stays in the envelope" in entry.text


def test_share_auction(orange_red):
    # Orange: £900 / 100 shares = £9; 10 new shares open at £90
    with pytest.raises(RuleError, match="bidding starts at £90"):
        orange_red.record(("share_auction", {"company": "c1", "shares": 10, "winner": "p3",
                                             "bid": 80, "yes": ["p2"]}))
    state, _ = orange_red.record(("share_auction", {"company": "c1", "shares": 10, "winner": "p3",
                                                    "bid": 95, "yes": ["p2"]}))
    c = state.companies["c1"]
    assert c.holdings == {"p1": 40, "p2": 60, "p3": 10}
    assert c.cash == 340 + 95


def test_going_private_on_trade(orange_red):
    state, [entry] = orange_red.record(("share_transfer", {"company": "c1", "from": "p2",
                                                           "to": "p1", "shares": 60, "price": 500}))
    c = state.companies["c1"]
    assert c.status == "private" and c.successor == "p1"
    assert all(d.owner == "p1" for d in state.group_deeds("ORANGE"))
    assert "went private" in entry.effects[0]
    # The colour can be founded again later
    assert any(o.group == "ORANGE" and not o.eligible and "one player" in o.reason
               for o in state.founding_options())


def test_company_buildings_need_vote_and_cash(orange_red):
    houses = {"bow_street": 1, "marlborough_street": 1, "vine_street": 2}
    with pytest.raises(RuleError, match="Build evenly"):
        orange_red.record(("set_houses", {"group": "ORANGE", "yes": ["p2"],
                                          "houses": {**houses, "vine_street": 3}}))
    with pytest.raises(RuleError, match="motion fails"):
        orange_red.record(("set_houses", {"group": "ORANGE", "yes": ["p1"], "houses": houses}))
    # Four houses at £100 is more than the £340 tracked in the envelope
    with pytest.raises(RuleError, match="Count the envelope"):
        orange_red.record(("set_houses", {"group": "ORANGE", "yes": ["p2"], "houses": houses}))
    state, _ = orange_red.record(("envelope_count", {"company": "c1", "cash": 500}),
                                 ("set_houses", {"group": "ORANGE", "yes": ["p2"], "houses": houses}))
    c = state.companies["c1"]
    assert c.cash == 100
    assert state.company_value(c) == 100 + 560 + 400  # cash turned into buildings at cost
    state, _ = orange_red.record(("set_houses", {"group": "ORANGE", "yes": ["p2"],
                                                 "houses": {"vine_street": 1}}))
    assert state.companies["c1"].cash == 150  # one house sold back at half price


def test_envelope_count_note(orange_red):
    state, [entry] = orange_red.record(("envelope_count", {"company": "c1", "cash": 390,
                                                           "note": " Bob gifted £50 "}))
    assert state.companies["c1"].cash == 390
    assert entry.text.endswith("; note: Bob gifted £50")
    with pytest.raises(RuleError, match="under 200"):
        orange_red.record(("envelope_count", {"company": "c1", "cash": 390, "note": "x" * 201}))


def test_company_mortgage_flows(orange_red):
    state, _ = orange_red.record(("set_mortgage", {"deed": "vine_street", "mortgaged": True,
                                                   "yes": ["p2"]}))
    assert state.companies["c1"].cash == 440
    state, _ = orange_red.record(("set_mortgage", {"deed": "vine_street", "mortgaged": False,
                                                   "yes": ["p2"]}))
    assert state.companies["c1"].cash == 440 - 110


def test_player_buildings_and_trading_rules(store):
    own(store, "p1", "park_lane", "mayfair")
    store.record(("set_houses", {"group": "DKBLUE", "houses": {"park_lane": 1, "mayfair": 1}}))
    with pytest.raises(RuleError, match="Sell the buildings"):
        store.record(("set_owner", {"deed": "mayfair", "owner": "p2"}))
    with pytest.raises(RuleError, match="Sell the buildings"):
        store.record(("set_mortgage", {"deed": "mayfair", "mortgaged": True}))


def test_bankruptcy_to_the_bank(orange_red):
    # Bob is CEO of nothing, but holds Orange and Red shares and owns no loose deeds.
    own(orange_red, "p2", "pall_mall")
    state, [entry] = orange_red.record(("bankruptcy", {
        "player": "p2", "creditor": {"kind": "company", "id": "c2"}, "cash": 12,
        "deeds": {"pall_mall": {"buyer": "p3", "price": 70}},
        "shares": {"c1": {"buyer": "p3", "price": 400}},  # Red shares get no bids
    }))
    assert state.players["p2"].bankrupt
    assert state.deeds["pall_mall"].owner == "p3"
    assert state.companies["c1"].holdings == {"p1": 40, "p3": 60}
    # Bob's 30 Red shares are cancelled, so Carol owns every share and Red goes private
    red = state.companies["c2"]
    assert red.status == "private" and red.successor == "p3"
    assert any("went private" in e for e in entry.effects)


def test_bankruptcy_moves_ceo_to_biggest_holder(orange_red):
    state, [entry] = orange_red.record(("bankruptcy", {
        "player": "p1", "creditor": {"kind": "bank"},
        "shares": {"c1": {"buyer": "p3", "price": 300}}}))
    orange = state.companies["c1"]
    assert orange.holdings == {"p2": 60, "p3": 40}
    assert orange.ceo == "p2"
    assert "New CEO of Orange Inc.: Bob (biggest shareholder)" in entry.effects


def test_bankruptcy_to_player_takes_everything(orange_red):
    state, _ = orange_red.record(("bankruptcy", {"player": "p1",
                                                 "creditor": {"kind": "player", "id": "p3"}}))
    assert state.companies["c1"].holdings == {"p2": 60, "p3": 40}
    assert state.companies["c1"].ceo == "p2"


def test_void_refuses_to_break_later_events(orange_red):
    events = orange_red.events()
    founding = next(ev for ev in events if ev.type == "found")
    orange_red.record(("share_transfer", {"company": "c1", "from": "p1", "to": "p3", "shares": 5}))
    with pytest.raises(RuleError, match="would no longer be valid"):
        orange_red.set_voided(founding.id, True)
    state, last = orange_red.undo_last()
    assert last.type == "share_transfer"
    assert state.companies["c1"].holdings == {"p1": 40, "p2": 60}
    state = orange_red.set_voided(last.id, False)
    assert state.companies["c1"].holdings == {"p1": 35, "p2": 60, "p3": 5}


def test_void_middle_event_replays_the_rest(store):
    own(store, "p1", "old_kent_road")
    own(store, "p2", "euston_road")
    first = store.events()[1]
    state = store.set_voided(first.id, True)
    assert state.deeds["old_kent_road"].owner is None
    assert state.deeds["euston_road"].owner == "p2"


def test_ids_are_never_reused(orange_red):
    assert orange_red.next_id("c") == "c3"
    assert orange_red.next_id("p") == "p4"


def test_export_import_roundtrip(orange_red):
    orange_red.undo_last()
    exported = orange_red.export()
    state = orange_red.import_events(exported["events"])
    assert "c2" not in state.companies
    assert len(orange_red.events()) == len(exported["events"])


def test_net_worth(orange_red):
    state, _ = orange_red.record(("final_tally", {"cash": {"p1": 100, "p2": 0, "p3": 50}}))
    rows = {r["player"].id: r for r in state.net_worth(state.final_cash)}
    assert rows["p1"]["total"] == 100 + 40 * 9
    assert rows["p2"]["total"] == 60 * 9 + 30 * 15
    assert rows["p3"]["total"] == 50 + 70 * 15
