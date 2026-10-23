"""Static board data (deeds, colour groups), loaded from data/<edition>.json."""

import json
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@dataclass(frozen=True)
class Group:
    id: str
    name: str
    colour: str
    company: str  # default company name when this colour is founded
    house_cost: int

    @property
    def buildable(self) -> bool:
        return self.house_cost > 0


@dataclass(frozen=True)
class DeedInfo:
    id: str
    name: str
    group: str
    price: int
    square: int


@dataclass(frozen=True)
class Board:
    edition: str
    currency: str
    houses_in_bank: int
    hotels_in_bank: int
    groups: dict[str, Group]
    deeds: dict[str, DeedInfo]  # in board order

    def group_deeds(self, group_id: str) -> list[DeedInfo]:
        return [d for d in self.deeds.values() if d.group == group_id]


def load_board(edition: str = "uk") -> Board:
    raw = json.loads((DATA_DIR / f"{edition}.json").read_text(encoding="utf-8"))
    groups = {g["id"]: Group(**g) for g in raw["groups"]}
    deeds = sorted((DeedInfo(**d) for d in raw["deeds"]), key=lambda d: d.square)
    for d in deeds:
        if d.group not in groups:
            raise ValueError(f"deed {d.id} has unknown group {d.group}")
    return Board(
        edition=raw["edition"],
        currency=raw["currency"],
        houses_in_bank=raw["houses_in_bank"],
        hotels_in_bank=raw["hotels_in_bank"],
        groups=groups,
        deeds={d.id: d for d in deeds},
    )
