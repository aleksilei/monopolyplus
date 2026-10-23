"""Game state, derived by folding the append-only event log.

Every change to the game is an Event. `fold` replays the non-voided events in
order. Each handler checks the rules against the state so far and raises
RuleError if the event isn't allowed, so replaying after a void also tells us
whether the void would break a later event.

Ids are fixed when an event is recorded (players "p1", companies "c1", ...) and
stored in its payload, so voiding one event never renumbers another.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction

from . import rules
from .board import Board
from .fmt import money, names, price


class RuleError(Exception):
    """The rules don't allow this event in the current state."""

    def __init__(self, message: str, event: Event | None = None):
        super().__init__(message)
        self.event = event


@dataclass
class Event:
    id: int | None
    type: str
    payload: dict
    created_at: str  # ISO 8601, UTC
    voided: bool = False


@dataclass
class Settings:
    starting_cash: int = 1500
    founding_shares: int = 100
    extra_meeting_threshold: Fraction = Fraction(1, 4)
    vote_threshold: Fraction = Fraction(1, 2)
    auction_start_fraction: Fraction = Fraction(1)
    time_cap_minutes: int = 180

    INTS = ("starting_cash", "founding_shares", "time_cap_minutes")
    FRACTIONS = ("extra_meeting_threshold", "vote_threshold", "auction_start_fraction")

    def update(self, raw: dict) -> list[str]:
        """Apply settings from a payload; returns a description of what changed."""
        changed = []
        for key, value in raw.items():
            if key in self.INTS:
                value = int(value)
                if value <= 0:
                    raise RuleError(f"{key} must be positive")
            elif key in self.FRACTIONS:
                value = Fraction(str(value))
                if not 0 < value <= 2:
                    raise RuleError(f"{key} is out of range")
            else:
                raise RuleError(f"Unknown setting {key}")
            if getattr(self, key) != value:
                setattr(self, key, value)
                changed.append(key)
        return changed

    def to_payload(self) -> dict:
        return {k: (str(getattr(self, k)) if k in self.FRACTIONS else getattr(self, k))
                for k in self.INTS + self.FRACTIONS}


@dataclass
class Player:
    id: str
    name: str
    bankrupt: bool = False


@dataclass
class Deed:
    id: str
    name: str
    group: str
    price: int
    house_cost: int
    owner: str | None = None  # player id, company id, or None for the bank
    houses: int = 0  # 5 = hotel
    mortgaged: bool = False

    @property
    def value(self) -> Fraction:
        return rules.deed_value(self.price, self.house_cost, self.houses, self.mortgaged)


@dataclass
class Company:
    id: str
    name: str
    ceo: str
    groups: list[str]
    founded_round: int
    cash: int = 0  # envelope: last count plus every flow the banker recorded since
    counted_round: int | None = None
    counted_at: str | None = None
    holdings: dict[str, int] = field(default_factory=dict)  # player id -> shares, play order
    status: str = "active"  # active | merged | private | dissolved
    successor: str | None = None  # company merged into, or player who took it private
    ceo_tie: bool = False

    @property
    def active(self) -> bool:
        return self.status == "active"

    @property
    def outstanding(self) -> int:
        return sum(self.holdings.values())


@dataclass
class LogEntry:
    event: Event
    round: int
    text: str
    effects: list[str]


@dataclass
class FoundingOption:
    group: str
    eligible: bool
    reason: str
    founders: dict[str, list[Deed]]  # player id -> their deeds of this colour


class State:
    def __init__(self, board: Board):
        self.board = board
        self.settings = Settings()
        self.players: dict[str, Player] = {}
        self.deeds: dict[str, Deed] = {
            d.id: Deed(d.id, d.name, d.group, d.price, board.groups[d.group].house_cost)
            for d in board.deeds.values()
        }
        self.companies: dict[str, Company] = {}
        self.round = 1
        self.started_at: str | None = None
        self.final_cash: dict[str, int] | None = None
        self.final_at: str | None = None
        self.log: list[LogEntry] = []

    # --- lookups ---------------------------------------------------------------

    def name(self, ref: str | None) -> str:
        if ref is None:
            return "the bank"
        if ref in self.players:
            return self.players[ref].name
        if ref in self.companies:
            return self.companies[ref].name
        return ref

    def player(self, pid, *, active: bool = True) -> Player:
        p = self.players.get(pid)
        if p is None:
            raise RuleError(f"Unknown player {pid!r}")
        if active and p.bankrupt:
            raise RuleError(f"{p.name} is bankrupt")
        return p

    def company(self, cid, *, active: bool = True) -> Company:
        c = self.companies.get(cid)
        if c is None:
            raise RuleError(f"Unknown company {cid!r}")
        if active and not c.active:
            raise RuleError(f"{c.name} no longer exists ({c.status})")
        return c

    def deed(self, did) -> Deed:
        d = self.deeds.get(did)
        if d is None:
            raise RuleError(f"Unknown deed {did!r}")
        return d

    @property
    def active_players(self) -> list[Player]:
        return [p for p in self.players.values() if not p.bankrupt]

    @property
    def active_companies(self) -> list[Company]:
        return [c for c in self.companies.values() if c.active]

    def group_deeds(self, gid: str) -> list[Deed]:
        return [d for d in self.deeds.values() if d.group == gid]

    def group_owner(self, gid: str) -> str | None:
        """Whoever owns every deed of the colour, else None."""
        owners = {d.owner for d in self.group_deeds(gid)}
        return owners.pop() if len(owners) == 1 else None

    def deeds_of(self, owner: str) -> list[Deed]:
        return [d for d in self.deeds.values() if d.owner == owner]

    def company_value(self, c: Company) -> Fraction:
        return rules.company_value(c.cash, (d.value for d in self.deeds_of(c.id)))

    def share_price(self, c: Company) -> Fraction:
        return rules.share_price(self.company_value(c), c.outstanding)

    def holdings_of(self, pid: str) -> dict[str, int]:
        return {c.id: c.holdings[pid] for c in self.active_companies if c.holdings.get(pid)}

    def buildings_in_use(self) -> tuple[int, int]:
        houses = sum(d.houses for d in self.deeds.values() if d.houses < 5)
        hotels = sum(1 for d in self.deeds.values() if d.houses == 5)
        return houses, hotels

    def winner(self) -> Player | None:
        active = self.active_players
        return active[0] if len(active) == 1 and len(self.players) > 1 else None

    def yes_shares(self, c: Company, yes) -> int:
        return sum(c.holdings.get(pid, 0) for pid in set(yes or []))

    def vote_passes(self, c: Company, yes) -> bool:
        return rules.vote_passes(self.yes_shares(c, yes), c.outstanding,
                                 self.settings.vote_threshold)

    def can_call_extra_meeting(self, c: Company, pid: str) -> bool:
        return rules.can_call_extra_meeting(c.holdings.get(pid, 0), c.outstanding,
                                            self.settings.extra_meeting_threshold)

    def auction_start_bid(self, c: Company, shares: int) -> int:
        return rules.auction_start_bid(shares, self.share_price(c),
                                       self.settings.auction_start_fraction)

    def _require_vote(self, c: Company, yes) -> str:
        got = self.yes_shares(c, yes)
        if not self.vote_passes(c, yes):
            needed = float(self.settings.vote_threshold * c.outstanding)
            raise RuleError(f"The motion fails at {c.name}: {got} of {c.outstanding} shares "
                            f"voted yes, more than {needed:g} needed")
        return f"{got}/{c.outstanding} yes"

    def _require_cash(self, c: Company, amount: int, what: str) -> None:
        if amount > c.cash:
            counted = f"counted in round {c.counted_round}" if c.counted_round else "never counted"
            raise RuleError(
                f"{c.name}'s envelope shows {money(c.cash)} ({counted}) but {what} needs "
                f"{money(amount)}. Count the envelope if it holds more.")

    def _set_holding(self, c: Company, pid: str, shares: int) -> None:
        c.holdings[pid] = shares
        c.holdings = {p: c.holdings[p] for p in self.players if c.holdings.get(p, 0) > 0}

    # --- previews shared by handlers and the UI --------------------------------

    def founding_options(self) -> list[FoundingOption]:
        out = []
        for gid in self.board.groups:
            deeds = self.group_deeds(gid)
            founders: dict[str, list[Deed]] = {}
            for pid in self.players:
                mine = [d for d in deeds if d.owner == pid]
                if mine:
                    founders[pid] = mine
            company_owner = next((d.owner for d in deeds if d.owner in self.companies), None)
            if company_owner:
                reason, ok = f"owned by {self.name(company_owner)}", False
            elif any(d.owner is None for d in deeds):
                reason, ok = "the bank still holds some deeds", False
            elif len(founders) < 2:
                reason, ok = "one player owns the whole set (needs 2+ players)", False
            else:
                reason, ok = f"{names(self.name(p) for p in founders)} can found it", True
            out.append(FoundingOption(gid, ok, reason, founders))
        return out

    def founding_preview(self, gid: str, cash: dict, ceo: str | None) -> dict:
        option = next((o for o in self.founding_options() if o.group == gid), None)
        if option is None:
            raise RuleError(f"Unknown colour {gid!r}")
        if not option.eligible:
            raise RuleError(f"{self.board.groups[gid].name} can't be founded: {option.reason}")
        founders = option.founders
        for pid, amount in cash.items():
            if pid not in founders:
                raise RuleError(f"{self.name(pid)} owns no {self.board.groups[gid].name} deed")
            if int(amount) < 0:
                raise RuleError("Cash contributions can't be negative")
        if ceo not in founders:
            raise RuleError("The CEO must be one of the founders")
        rows = {}
        for pid, deeds in founders.items():
            deed_value = sum((d.value for d in deeds), Fraction(0))
            put_cash = int(cash.get(pid, 0) or 0)
            rows[pid] = {"deeds": deeds, "deed_value": deed_value, "cash": put_cash,
                         "total": deed_value + put_cash}
        shares = rules.split_shares({p: r["total"] for p, r in rows.items()},
                                    self.settings.founding_shares, prefer=ceo)
        for pid, r in rows.items():
            r["shares"] = shares[pid]
        value = sum((r["total"] for r in rows.values()), Fraction(0))
        return {"rows": rows, "cash": sum(r["cash"] for r in rows.values()), "value": value,
                "price": rules.share_price(value, self.settings.founding_shares)}

    def merger_preview(self, a_id: str, b_id: str, ceo: str | None = None) -> dict:
        a, b = self.company(a_id), self.company(b_id)
        if a.id == b.id:
            raise RuleError("Pick two different companies")
        price_a, price_b = self.share_price(a), self.share_price(b)
        values = rules.merger_values(a.holdings, price_a, b.holdings, price_b)
        tied = rules.biggest(values)
        if len(tied) == 1:
            ceo = tied[0]
        elif ceo not in tied:
            ceo = None
        shares = rules.split_shares(values, self.settings.founding_shares,
                                    prefer=ceo if ceo else tied[0])
        value = self.company_value(a) + self.company_value(b)
        return {"a": a, "b": b, "price_a": price_a, "price_b": price_b, "values": values,
                "shares": shares, "tied": tied, "ceo": ceo, "value": value,
                "price": rules.share_price(value, self.settings.founding_shares)}

    def net_worth(self, cash: dict[str, int]) -> list[dict]:
        """Net worth per active player, richest first."""
        rows = []
        for p in self.active_players:
            deeds = sum((d.value for d in self.deeds_of(p.id)), Fraction(0))
            shares = sum((n * self.share_price(self.companies[cid])
                          for cid, n in self.holdings_of(p.id).items()), Fraction(0))
            c = int(cash.get(p.id, 0) or 0)
            rows.append({"player": p, "cash": c, "deeds": deeds, "shares": shares,
                         "total": c + deeds + shares})
        rows.sort(key=lambda r: r["total"], reverse=True)
        return rows

    # --- applying events ---------------------------------------------------------

    def apply(self, ev: Event) -> LogEntry:
        handler = HANDLERS.get(ev.type)
        try:
            if handler is None:
                raise RuleError(f"Unknown event type {ev.type!r}")
            if (ev.type == "game_start") == (self.started_at is not None):
                raise RuleError("game_start must be the first event, and only the first")
            effects: list[str] = []
            had_winner = self.winner() is not None
            text = handler(self, ev.payload, ev, effects)
            self._settle(effects, announce_winner=not had_winner)
        except RuleError as e:
            e.event = ev
            raise
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            raise RuleError(f"Malformed {ev.type} event: {e!r}", ev) from e
        entry = LogEntry(ev, self.round, text, effects)
        self.log.append(entry)
        return entry

    def _settle(self, effects: list[str], announce_winner: bool) -> None:
        """Consequences that follow automatically from any event."""
        for c in self.active_companies:
            ceo = self.players.get(c.ceo)
            if ceo is not None and ceo.bankrupt and c.holdings:
                tied = rules.biggest(c.holdings)
                c.ceo = tied[0]
                c.ceo_tie = len(tied) > 1
                if c.ceo_tie:
                    effects.append(f"{c.name}: CEO tie between "
                                   f"{names(self.name(p) for p in tied)}. Roll the dice, "
                                   f"then set the CEO on the company page.")
                else:
                    effects.append(f"New CEO of {c.name}: {self.name(c.ceo)} (biggest shareholder)")
            owner = rules.go_private_owner(c.holdings)
            if owner is not None:
                deeds = self.deeds_of(c.id)
                for d in deeds:
                    d.owner = owner
                effects.append(
                    f"{c.name} went private: give {self.name(owner)} its envelope "
                    f"({money(c.cash)} when last tracked) and its deeds "
                    f"({', '.join(d.name for d in deeds) or 'none'}).")
                c.status, c.successor, c.cash, c.holdings = "private", owner, 0, {}
            elif not c.holdings:
                for d in self.deeds_of(c.id):
                    d.owner, d.houses, d.mortgaged = None, 0, False
                effects.append(f"{c.name} has no shareholders left: its deeds go back to the bank.")
                c.status, c.cash = "dissolved", 0
        winner = self.winner()
        if winner is not None and announce_winner:
            effects.append(f"{winner.name} is the last player standing and wins!")


def fold(board: Board, events: list[Event]) -> State:
    state = State(board)
    for ev in events:
        if not ev.voided:
            state.apply(ev)
    return state


# --- event handlers ----------------------------------------------------------------
# Each takes (state, payload, event, effects), validates everything first, then
# mutates the state and returns a one-line description for the history.

def _int(value, what: str, minimum: int = 0) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise RuleError(f"{what} must be a whole number") from None
    if n < minimum:
        raise RuleError(f"{what} must be at least {minimum}")
    return n


def _game_start(s: State, p: dict, ev: Event, effects) -> str:
    players = p["players"]
    if len(players) < 2:
        raise RuleError("A game needs at least two players")
    seen = set()
    for pl in players:
        name = pl["name"].strip()
        if not name or name.lower() in seen:
            raise RuleError("Player names must be non-empty and different")
        seen.add(name.lower())
    s.settings.update(p.get("settings", {}))
    for pl in players:
        s.players[pl["id"]] = Player(pl["id"], pl["name"].strip())
    s.started_at = ev.created_at
    return f"New game: {names(pl.name for pl in s.players.values())}"


def _settings(s: State, p: dict, ev: Event, effects) -> str:
    changed = s.settings.update(p["settings"])
    if not changed:
        raise RuleError("No settings changed")
    return "Settings changed: " + ", ".join(k.replace("_", " ") for k in changed)


def _add_player(s: State, p: dict, ev: Event, effects) -> str:
    pid, name = p["player"], p["name"].strip()
    if pid in s.players:
        raise RuleError(f"Player id {pid} already exists")
    if not name or any(pl.name.lower() == name.lower() for pl in s.players.values()):
        raise RuleError("Player names must be non-empty and different")
    s.players[pid] = Player(pid, name)
    return f"{name} joined the game"


def _rename_player(s: State, p: dict, ev: Event, effects) -> str:
    pl = s.player(p["player"], active=False)
    name = p["name"].strip()
    if not name or any(o.name.lower() == name.lower() for o in s.players.values() if o is not pl):
        raise RuleError("Player names must be non-empty and different")
    old, pl.name = pl.name, name
    return f"{old} is now called {name}"


def _round(s: State, p: dict, ev: Event, effects) -> str:
    s.round = _int(p["round"], "Round", 1)
    return f"Round {s.round}"


def _set_owner(s: State, p: dict, ev: Event, effects) -> str:
    d = s.deed(p["deed"])
    new = p.get("owner") or None
    if d.owner in s.companies:
        raise RuleError(f"{d.name} belongs to {s.name(d.owner)}; companies only "
                        "gain or lose deeds by founding, merging or going private")
    if new is not None:
        s.player(new)
    if new == d.owner:
        raise RuleError(f"{s.name(new)} already owns {d.name}")
    if any(g.houses for g in s.group_deeds(d.group)):
        raise RuleError(f"Sell the buildings on {s.board.groups[d.group].name} before "
                        "the deeds change hands")
    old = d.owner
    d.owner = new
    if new is None:
        d.mortgaged = False
    return f"{d.name}: {s.name(old)} → {s.name(new)}"


def _set_houses(s: State, p: dict, ev: Event, effects) -> str:
    gid = p["group"]
    group = s.board.groups.get(gid)
    if group is None or not group.buildable:
        raise RuleError("You can't build on that colour")
    deeds = s.group_deeds(gid)
    owner = s.group_owner(gid)
    if owner is None:
        raise RuleError(f"Only someone who owns all of {group.name} can build there")
    target = {d.id: _int(p["houses"].get(d.id, d.houses), d.name, 0) for d in deeds}
    if any(n > 5 for n in target.values()):
        raise RuleError("At most 4 houses or a hotel per deed")
    if any(target.values()) and any(d.mortgaged for d in deeds):
        raise RuleError(f"Unmortgage all of {group.name} before building")
    if not rules.even_build_ok(target.values()):
        raise RuleError("Build evenly: deeds in a colour may differ by at most one building")
    built = sum(max(0, target[d.id] - d.houses) for d in deeds)
    sold = sum(max(0, d.houses - target[d.id]) for d in deeds)
    if not built and not sold:
        raise RuleError("No change to the buildings")
    cost = built * group.house_cost - sold * rules.house_sale_refund(group.house_cost)
    vote = ""
    company = s.companies.get(owner)
    if company is not None:
        s.company(owner)
        vote = f" ({s._require_vote(company, p.get('yes'))})"
        s._require_cash(company, cost, "this building work")
    else:
        s.player(owner)
    for d in deeds:
        d.houses = target[d.id]
    layout = ", ".join(f"{d.name} {_buildings(d.houses)}" for d in deeds)
    if company is not None:
        company.cash -= cost
        flow = (f"{money(cost)} from the envelope to the bank" if cost >= 0
                else f"bank pays {money(-cost)} into the envelope")
    else:
        flow = (f"{s.name(owner)} pays the bank {money(cost)}" if cost >= 0
                else f"bank pays {s.name(owner)} {money(-cost)}")
    return f"{s.name(owner)} buildings{vote}: {layout}; {flow}"


def _buildings(n: int) -> str:
    return "hotel" if n == 5 else f"{n} house{'s' if n != 1 else ''}"


def _set_mortgage(s: State, p: dict, ev: Event, effects) -> str:
    d = s.deed(p["deed"])
    want = bool(p["mortgaged"])
    if d.owner is None:
        raise RuleError(f"The bank owns {d.name}")
    if d.mortgaged == want:
        raise RuleError(f"{d.name} is already {'mortgaged' if want else 'unmortgaged'}")
    if want and any(g.houses for g in s.group_deeds(d.group)):
        raise RuleError(f"Sell the buildings on {s.board.groups[d.group].name} before mortgaging")
    amount = rules.mortgage_value(d.price) if want else -rules.unmortgage_cost(d.price)
    company = s.companies.get(d.owner)
    vote = ""
    if company is not None:
        s.company(d.owner)
        vote = f" ({s._require_vote(company, p.get('yes'))})"
        s._require_cash(company, -amount, "unmortgaging")
        company.cash += amount
        flow = (f"bank pays {money(amount)} into the envelope" if want
                else f"{money(-amount)} from the envelope to the bank")
    else:
        s.player(d.owner)
        flow = (f"bank pays {s.name(d.owner)} {money(amount)}" if want
                else f"{s.name(d.owner)} pays the bank {money(-amount)}")
    d.mortgaged = want
    verb = "mortgaged" if want else "unmortgaged"
    return f"{s.name(d.owner)} {verb} {d.name}{vote}; {flow}"


def _envelope_count(s: State, p: dict, ev: Event, effects) -> str:
    c = s.company(p["company"])
    cash = _int(p["cash"], "Envelope cash", 0)
    before = c.cash
    c.cash, c.counted_round, c.counted_at = cash, s.round, ev.created_at
    diff = "" if cash == before else f" (tracked {money(before)}, {money(cash - before)} difference)"
    return f"Counted {c.name}'s envelope: {money(cash)}{diff}"


def _found(s: State, p: dict, ev: Event, effects) -> str:
    cid, gid = p["company"], p["group"]
    if cid in s.companies:
        raise RuleError(f"Company id {cid} already exists")
    name = (p.get("name") or "").strip() or s.board.groups[gid].company
    if any(c.name.lower() == name.lower() for c in s.active_companies):
        raise RuleError(f"There's already a company called {name}")
    cash = {pid: _int(v or 0, f"{s.name(pid)}'s cash", 0) for pid, v in p.get("cash", {}).items()}
    preview = s.founding_preview(gid, cash, p["ceo"])
    company = Company(id=cid, name=name, ceo=p["ceo"], groups=[gid], founded_round=s.round,
                      cash=preview["cash"], counted_round=s.round, counted_at=ev.created_at)
    for pid, row in preview["rows"].items():
        for d in row["deeds"]:
            d.owner = cid
        s._set_holding(company, pid, row["shares"])
    s.companies[cid] = company
    split = ", ".join(f"{s.name(pid)} {r['shares']}" for pid, r in preview["rows"].items())
    return (f"Founded {name} ({s.board.groups[gid].name}): {split} shares at "
            f"{price(preview['price'])}; envelope {money(preview['cash'])}; "
            f"CEO {s.name(p['ceo'])}")


def _share_transfer(s: State, p: dict, ev: Event, effects) -> str:
    c = s.company(p["company"])
    src, dst = s.player(p["from"]), s.player(p["to"])
    n = _int(p["shares"], "Shares", 1)
    if src.id == dst.id:
        raise RuleError("Seller and buyer must be different players")
    if c.holdings.get(src.id, 0) < n:
        raise RuleError(f"{src.name} holds only {c.holdings.get(src.id, 0)} {c.name} shares")
    paid = p.get("price")
    s._set_holding(c, src.id, c.holdings[src.id] - n)
    s._set_holding(c, dst.id, c.holdings.get(dst.id, 0) + n)
    for_ = f" for {money(_int(paid, 'Price'))}" if paid not in (None, "") else ""
    return f"{src.name} sold {n} {c.name} shares to {dst.name}{for_}"


def _share_auction(s: State, p: dict, ev: Event, effects) -> str:
    c = s.company(p["company"])
    n = _int(p["shares"], "New shares", 1)
    winner = s.player(p["winner"])
    bid = _int(p["bid"], "Winning bid", 0)
    vote = s._require_vote(c, p.get("yes"))
    start = s.auction_start_bid(c, n)
    if bid < start:
        raise RuleError(f"The bidding starts at {money(start)} ({n} × {price(s.share_price(c))})")
    c.cash += bid
    s._set_holding(c, winner.id, c.holdings.get(winner.id, 0) + n)
    return (f"{c.name} issued {n} new shares ({vote}); {winner.name} won the auction at "
            f"{money(bid)} (opening bid {money(start)}), paid into the envelope")


def _dividend(s: State, p: dict, ev: Event, effects) -> str:
    c = s.company(p["company"])
    amount = _int(p["amount"], "Dividend", 1)
    vote = s._require_vote(c, p.get("yes"))
    s._require_cash(c, amount, "this dividend")
    payouts, remainder = rules.dividend_split(amount, c.holdings)
    c.cash -= amount - remainder
    paid = ", ".join(f"{s.name(pid)} {money(v)}" for pid, v in payouts.items())
    left = f"; {money(remainder)} stays in the envelope" if remainder else ""
    return f"{c.name} paid a {money(amount)} dividend ({vote}): {paid}{left}"


def _merge(s: State, p: dict, ev: Event, effects) -> str:
    cid = p["company"]
    if cid in s.companies:
        raise RuleError(f"Company id {cid} already exists")
    name = (p.get("name") or "").strip()
    if not name:
        raise RuleError("The new company needs a name")
    preview = s.merger_preview(p["a"], p["b"], p.get("ceo"))
    a, b = preview["a"], preview["b"]
    if any(c.name.lower() == name.lower() for c in s.active_companies if c not in (a, b)):
        raise RuleError(f"There's already a company called {name}")
    if preview["ceo"] is None:
        raise RuleError(f"Tie for biggest shareholder between "
                        f"{names(s.name(x) for x in preview['tied'])}: roll the dice and pick the CEO")
    vote_a = s._require_vote(a, p.get("yes_a"))
    vote_b = s._require_vote(b, p.get("yes_b"))
    counted = [c for c in (a, b) if c.counted_round is not None]
    oldest = min(counted, key=lambda c: c.counted_round) if len(counted) == 2 else None
    new = Company(id=cid, name=name, ceo=preview["ceo"], groups=a.groups + b.groups,
                  founded_round=s.round, cash=a.cash + b.cash,
                  counted_round=oldest.counted_round if oldest else None,
                  counted_at=oldest.counted_at if oldest else None)
    for d in s.deeds_of(a.id) + s.deeds_of(b.id):
        d.owner = cid
    for pid, n in preview["shares"].items():
        s._set_holding(new, pid, n)
    for old in (a, b):
        old.status, old.successor, old.cash, old.holdings = "merged", cid, 0, {}
    s.companies[cid] = new
    split = ", ".join(f"{s.name(pid)} {n}" for pid, n in new.holdings.items())
    return (f"{a.name} ({vote_a}) and {b.name} ({vote_b}) merged into {name}: {split} shares "
            f"at {price(preview['price'])}; CEO {s.name(new.ceo)}")


def _set_ceo(s: State, p: dict, ev: Event, effects) -> str:
    c = s.company(p["company"])
    pl = s.player(p["player"])
    if not c.holdings.get(pl.id):
        raise RuleError(f"{pl.name} holds no {c.name} shares")
    c.ceo, c.ceo_tie = pl.id, False
    return f"{pl.name} is CEO of {c.name}"


def _rename_company(s: State, p: dict, ev: Event, effects) -> str:
    c = s.company(p["company"])
    name = p["name"].strip()
    if not name or any(o.name.lower() == name.lower() for o in s.active_companies if o is not c):
        raise RuleError("Company names must be non-empty and different")
    old, c.name = c.name, name
    return f"{old} is now called {name}"


def _bankruptcy(s: State, p: dict, ev: Event, effects) -> str:
    debtor = s.player(p["player"])
    kind = p["creditor"]["kind"]
    creditor_id = p["creditor"].get("id")
    deeds = s.deeds_of(debtor.id)
    holdings = s.holdings_of(debtor.id)

    if kind == "player":
        creditor = s.player(creditor_id)
        if creditor.id == debtor.id:
            raise RuleError("A player can't go bankrupt to themselves")
        for d in deeds:
            d.owner = creditor.id
        for cid, n in holdings.items():
            c = s.companies[cid]
            s._set_holding(c, creditor.id, c.holdings.get(creditor.id, 0) + n)
            s._set_holding(c, debtor.id, 0)
        debtor.bankrupt = True
        got = [d.name for d in deeds] + [f"{n} {s.name(cid)} shares" for cid, n in holdings.items()]
        return (f"{debtor.name} went bankrupt to {creditor.name}, who takes their cash"
                f"{' and ' + ', '.join(got) if got else ''}")

    if kind not in ("bank", "company"):
        raise RuleError("The creditor must be a player, the bank or a company")
    company = s.company(creditor_id) if kind == "company" else None
    deed_sales, share_sales = p.get("deeds", {}), p.get("shares", {})
    if set(deed_sales) - {d.id for d in deeds}:
        raise RuleError(f"{debtor.name} doesn't own every deed being auctioned")
    if set(share_sales) - set(holdings):
        raise RuleError(f"{debtor.name} doesn't hold every share block being auctioned")

    def sale(entry):
        buyer = (entry or {}).get("buyer") or None
        if buyer is None:
            return None, 0
        if buyer == debtor.id:
            raise RuleError("The bankrupt player can't bid")
        s.player(buyer)
        return buyer, _int(entry.get("price"), "Auction price", 0)

    deed_results = {d.id: sale(deed_sales.get(d.id)) for d in deeds}
    share_results = {cid: sale(share_sales.get(cid)) for cid in holdings}
    cash = _int(p.get("cash") or 0, "Cash handed over", 0) if company else 0

    refund = sum(d.houses * rules.house_sale_refund(d.house_cost) for d in deeds)
    proceeds = sum(pr for _, pr in deed_results.values()) + sum(pr for _, pr in share_results.values())
    lines = []
    for d in deeds:
        buyer, paid = deed_results[d.id]
        d.houses = 0
        if buyer:
            d.owner = buyer
            lines.append(f"{d.name}{' (mortgaged)' if d.mortgaged else ''} → {s.name(buyer)} {money(paid)}")
        else:
            d.owner, d.mortgaged = None, False
            lines.append(f"{d.name} → bank (no bids)")
    for cid, n in holdings.items():
        c = s.companies[cid]
        buyer, paid = share_results[cid]
        s._set_holding(c, debtor.id, 0)
        if buyer:
            s._set_holding(c, buyer, c.holdings.get(buyer, 0) + n)
            lines.append(f"{n} {c.name} shares → {s.name(buyer)} {money(paid)}")
        else:
            lines.append(f"{n} {c.name} shares cancelled (no bids)")
    debtor.bankrupt = True
    total = proceeds + refund + cash
    if company is not None:
        company.cash += total
        dest = f"{money(total)} into {company.name}'s envelope"
    else:
        dest = f"{money(total)} to the bank"
    extra = f"; buildings sold back for {money(refund)}" if refund else ""
    extra += f"; {money(cash)} cash handed over" if cash else ""
    return (f"{debtor.name} went bankrupt to {s.name(creditor_id) if company else 'the bank'}. "
            f"Auction: {'; '.join(lines) or 'nothing to sell'}{extra}. Total {dest}")


def _final_tally(s: State, p: dict, ev: Event, effects) -> str:
    cash = {pid: _int(v or 0, f"{s.name(pid)}'s cash", 0) for pid, v in p["cash"].items()}
    for pid in cash:
        s.player(pid)
    missing = [pl.name for pl in s.active_players if pl.id not in cash]
    if missing:
        raise RuleError(f"Enter cash for {names(missing)}")
    s.final_cash, s.final_at = cash, ev.created_at
    top = s.net_worth(cash)[0]
    return f"Time limit: final count recorded; {top['player'].name} is richest with {money(top['total'])}"


HANDLERS = {
    "game_start": _game_start,
    "settings": _settings,
    "add_player": _add_player,
    "rename_player": _rename_player,
    "round": _round,
    "set_owner": _set_owner,
    "set_houses": _set_houses,
    "set_mortgage": _set_mortgage,
    "envelope_count": _envelope_count,
    "found": _found,
    "share_transfer": _share_transfer,
    "share_auction": _share_auction,
    "dividend": _dividend,
    "merge": _merge,
    "set_ceo": _set_ceo,
    "rename_company": _rename_company,
    "bankruptcy": _bankruptcy,
    "final_tally": _final_tally,
}
