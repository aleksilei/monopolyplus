import pytest

from app.board import load_board
from app.store import Store


@pytest.fixture
def store():
    s = Store(":memory:", load_board())
    s.new_game(["Alice", "Bob", "Carol"], {})
    return s


def own(store, owner, *deeds):
    store.record(*[("set_owner", {"deed": d, "owner": owner}) for d in deeds])


@pytest.fixture
def orange_red(store):
    """The workbook's merger example: Orange worth £900 (Alice 40, Bob 60) and
    Red worth £1,500 (Bob 30, Carol 70)."""
    own(store, "p1", "bow_street")
    own(store, "p2", "marlborough_street", "vine_street", "strand")
    own(store, "p3", "fleet_street", "trafalgar_square")
    store.record(("found", {"company": "c1", "group": "ORANGE", "name": "Orange Inc.",
                            "ceo": "p1", "cash": {"p1": 180, "p2": 160}}))
    store.record(("found", {"company": "c2", "group": "RED", "name": "Red Industries",
                            "ceo": "p3", "cash": {"p2": 230, "p3": 590}}))
    return store
