"""Walk a whole game through the banker screens with Flask's test client."""

import io
import json

import pytest

from app import create_app

HX = {"HX-Request": "true"}


@pytest.fixture
def client(tmp_path):
    app = create_app(db_path=str(tmp_path / "test.db"), host_ip="192.168.1.20")
    app.config["TESTING"] = True
    return app.test_client()


def ok(resp, *needles):
    assert resp.status_code == 200, resp.status_code
    body = resp.get_data(as_text=True)
    for needle in needles:
        assert needle in body, f"{needle!r} not in page"
    return body


def test_full_game(client):
    # No game yet: everything redirects to setup
    assert client.get("/").headers["Location"].endswith("/setup")
    ok(client.get("/setup"), "New game")
    ok(client.get("/books"), "No game yet")
    client.post("/setup/new", data={"players": "Alice\nBob\nCarol\n", "vote_threshold": "50"})
    ok(client.get("/"), "Board", "Old Kent Road", "Bank has 32 houses")

    # Board edits through htmx fragments
    for deed, owner in [("bow_street", "p1"), ("marlborough_street", "p1"), ("vine_street", "p2"),
                        ("strand", "p2"), ("fleet_street", "p3"), ("trafalgar_square", "p3"),
                        ("park_lane", "p3"), ("mayfair", "p3"), ("pall_mall", "p2")]:
        ok(client.post(f"/deeds/{deed}/owner", data={"owner": owner}, headers=HX), "group-")
    ok(client.post("/groups/DKBLUE/houses", data={"park_lane": "1", "mayfair": "1"}, headers=HX),
       "Carol pays the bank £400", 'id="bank-buildings" class="pill" hx-swap-oob="true"')
    ok(client.post("/deeds/mayfair/mortgage", headers=HX), "Sell the buildings")
    ok(client.post("/deeds/strand/mortgage", headers=HX), "bank pays Bob £110")
    ok(client.post("/deeds/strand/mortgage", headers=HX), "Bob pays the bank £121")

    # Found Orange (the workbook example)
    ok(client.get("/found"), "Alice &amp; Bob can found it")
    ok(client.get("/found?group=ORANGE"), "Orange Inc.")
    ok(client.post("/found/preview", data={"group": "ORANGE", "ceo": "p1", "cash-p1": "100"}, headers=HX),
       "<b>70</b>", "<b>30</b>", "£6.60")
    resp = client.post("/found", data={"group": "ORANGE", "name": "Orange Inc.", "ceo": "p1",
                                       "cash-p1": "100", "cash-p2": ""})
    assert resp.headers["Location"].endswith("/companies/c1")
    ok(client.get("/companies/c1"), "Founded Orange Inc.", "£660", "£6.60", "Hand the founders")
    client.post("/found", data={"group": "RED", "name": "Red Industries", "ceo": "p3",
                                "cash-p2": "300", "cash-p3": "200"})

    # Board meeting: preview, failing vote, dividend, auction
    body = ok(client.post("/companies/c1/preview", data={"yes": "p1", "dividend": "50",
                                                         "auction_shares": "10"}, headers=HX),
              "motion passes", "Alice <b>£35</b>", "Opening bid <b>£66</b>")
    assert 'hx-swap-oob="true"' in body
    ok(client.post("/companies/c1/motion", data={"motion": "dividend", "dividend": "50", "yes": "p2"}),
       "motion fails")
    client.post("/companies/c1/motion", data={"motion": "dividend", "dividend": "50", "yes": "p1"})
    ok(client.get("/companies/c1"), "paid a £50 dividend", "£50")
    client.post("/companies/c1/count", data={"cash": "200", "note": "Carol gifted £50"})
    ok(client.get("/history"), "note: Carol gifted £50")
    ok(client.post("/companies/c1/motion", data={"motion": "auction", "auction_shares": "10",
                                                 "auction_winner": "p3", "auction_bid": "10",
                                                 "yes": "p1"}), "bidding starts at")
    client.post("/companies/c1/motion", data={"motion": "auction", "auction_shares": "10",
                                              "auction_winner": "p3", "auction_bid": "200", "yes": "p1"})
    ok(client.get("/companies"), "Alice <b>70</b>", "Carol <b>10</b>", "Who holds what")

    # Share trade and a merger
    ok(client.get("/trade"), "Record trade")
    client.post("/trade", data={"company": "c1", "from": "p1", "to": "p2", "shares": "5", "price": "50"})
    ok(client.get("/merge?a=c1&b=c2"), "Votes in favour", "New company")
    ok(client.post("/merge/preview", data={"a": "c1", "b": "c2", "count_a": "400", "yes_a": "p1"},
                   headers=HX), "Orange Inc. board: 65 of 110")
    resp = client.post("/merge", data={"a": "c1", "b": "c2", "name": "Orange-Red Holdings",
                                       "yes_a": ["p1"], "yes_b": ["p3"]})
    assert resp.status_code == 302, resp.get_data(as_text=True)
    ok(client.get(resp.headers["Location"]), "Orange-Red Holdings", "merged into")

    # Bankruptcy to the bank with an auction
    ok(client.get("/bankruptcy?player=p2&creditor=bank"), "Auction everything",
       "Orange-Red Holdings shares", "Pall Mall")
    client.post("/bankruptcy", data={"player": "p2", "creditor": "bank",
                                     "deed-pall_mall-buyer": "p1", "deed-pall_mall-price": "90",
                                     "shares-c3-buyer": "p1", "shares-c3-price": "100"})
    ok(client.get("/history"), "went bankrupt to the bank")

    # Time limit
    client.post("/endgame", data={"cash-p1": "500", "cash-p3": "300", "count-c3": "650"})
    ok(client.get("/endgame"), "Result", "🏆")
    ok(client.get("/books"), "Final net worth", "Orange-Red Holdings")
    ok(client.get("/books/live"), "Companies")
    ok(client.get("/qr"), "<svg", "http://192.168.1.20:8080/books")

    # Undo, void, restore
    client.post("/undo")
    assert "Final net worth" not in client.get("/books").get_data(as_text=True)
    resp = client.post("/events/12/void", follow_redirects=True)
    ok(resp, "would no longer be valid")

    # Export, then import as a new game
    exported = client.get("/export.json")
    assert exported.headers["Content-Type"].startswith("application/json")
    ok(client.get("/export.csv"), "Orange-Red Holdings")
    data = {"file": (io.BytesIO(exported.data), "game.json")}
    ok(client.post("/import", data=data, content_type="multipart/form-data", follow_redirects=True),
       "Game imported")
    events = json.loads(exported.data)["events"]
    body = ok(client.get("/history"), "went bankrupt to the bank")
    assert body.count('<tr class="voided">') == sum(e["voided"] for e in events)


def test_setup_settings_and_players(client):
    client.post("/setup/new", data={"players": "Ann\nBen"})
    ok(client.post("/settings", data={"time_cap_minutes": "120", "vote_threshold": "50"},
                   follow_redirects=True), "Settings changed: time cap minutes")
    ok(client.post("/players/add", data={"name": "Cid"}, follow_redirects=True), "Cid joined")
    ok(client.post("/players/p3/rename", data={"name": "Cyd"}, follow_redirects=True), "Cid is now called Cyd")
    ok(client.post("/setup/new", data={"players": "X\nY"}, follow_redirects=True), "Tick the box")
    ok(client.post("/round", follow_redirects=True), "Round <b>2</b>")
