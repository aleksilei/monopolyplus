"""Banker screens and the public /books page.

Pages are server-rendered; htmx swaps fragments for inline board edits and
live previews. Every write goes through Store.record, which folds the log,
validates the new events and appends them.
"""

import csv
import io
import json
from fractions import Fraction
from functools import wraps

import segno
from flask import (Blueprint, Response, current_app, flash, g, redirect, render_template,
                   request, url_for)

from werkzeug.datastructures import MultiDict

from . import rules
from .state import Event, RuleError, Settings
from .store import Store, utcnow

bp = Blueprint("banker", __name__)


def store() -> Store:
    return current_app.extensions["store"]


def needs_game(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        g.state = store().state()
        if g.state is None:
            return redirect(url_for("banker.setup"))
        return view(*args, **kwargs)
    return wrapper


def is_htmx() -> bool:
    return request.headers.get("HX-Request") == "true"


def flash_entries(entries) -> None:
    for entry in entries:
        flash(entry.text, "ok")
        for effect in entry.effects:
            flash(effect, "warn")


def to_int(value, what: str) -> int | None:
    """Blank -> None; otherwise a whole number or a RuleError."""
    value = (value or "").strip().replace(",", "").lstrip("£")
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        raise RuleError(f"{what}: {value!r} is not a whole number") from None


def back(default: str):
    return redirect(request.referrer or default)


def books_url() -> str:
    host_ip = current_app.config["HOST_IP"]
    if host_ip:
        return f"http://{host_ip}:{current_app.config['PUBLIC_PORT']}{url_for('banker.books')}"
    return url_for("banker.books", _external=True)


@bp.app_errorhandler(RuleError)
def rule_error(e):
    """Anything not handled closer to the form (e.g. a stale link to a merged company)."""
    if is_htmx():
        return f'<p class="msg error">{e}</p>', 200
    flash(str(e), "error")
    return redirect(url_for("banker.board"))


@bp.app_context_processor
def inject():
    state = getattr(g, "state", None)
    alerts = []
    if state is not None:
        winner = state.winner()
        if winner:
            alerts.append(f"{winner.name} is the last player standing and wins!")
        for c in state.active_companies:
            if c.ceo_tie:
                alerts.append(f"{c.name}: CEO tie. Roll the dice, then set the CEO on its page.")
    return {"state": state, "alerts": alerts, "rules": rules}


# --- setup ---------------------------------------------------------------------------

@bp.get("/setup")
def setup():
    g.state = store().state()
    return render_template("setup.html", defaults=Settings())


def settings_from_form() -> dict:
    out = {}
    for key in Settings.INTS:
        v = to_int(request.form.get(key), key.replace("_", " "))
        if v is not None:
            out[key] = v
    for key in Settings.FRACTIONS:
        v = to_int(request.form.get(key), key.replace("_", " "))
        if v is not None:
            out[key] = str(Fraction(v, 100))
    return out


@bp.post("/setup/new")
def new_game():
    g.state = store().state()
    if g.state is not None and not request.form.get("confirm"):
        flash("Tick the box to confirm you want to end the current game.", "error")
        return redirect(url_for("banker.setup"))
    players = [n.strip() for n in request.form.get("players", "").splitlines() if n.strip()]
    try:
        store().new_game(players, settings_from_form())
    except RuleError as e:
        flash(str(e), "error")
        return redirect(url_for("banker.setup"))
    flash(f"New game with {', '.join(players)}. Good luck!", "ok")
    return redirect(url_for("banker.board"))


@bp.post("/settings")
@needs_game
def settings():
    try:
        _, entries = store().record(("settings", {"settings": settings_from_form()}))
        flash_entries(entries)
    except RuleError as e:
        flash(str(e), "error")
    return redirect(url_for("banker.setup"))


@bp.post("/players/add")
@needs_game
def add_player():
    try:
        _, entries = store().record(("add_player", {"player": store().next_id("p"),
                                                    "name": request.form.get("name", "")}))
        flash_entries(entries)
    except RuleError as e:
        flash(str(e), "error")
    return redirect(url_for("banker.setup"))


@bp.post("/players/<pid>/rename")
@needs_game
def rename_player(pid):
    try:
        _, entries = store().record(("rename_player", {"player": pid,
                                                       "name": request.form.get("name", "")}))
        flash_entries(entries)
    except RuleError as e:
        flash(str(e), "error")
    return redirect(url_for("banker.setup"))


@bp.get("/export.json")
@needs_game
def export_json():
    body = json.dumps(store().export(), indent=2, ensure_ascii=False)
    return Response(body, mimetype="application/json", headers={
        "Content-Disposition": f"attachment; filename=monopoly-plus-{utcnow()[:10]}.json"})


@bp.get("/export.csv")
@needs_game
def export_csv():
    s = g.state
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Companies"])
    w.writerow(["Company", "Status", "CEO", "Envelope", "Last counted round", "Value",
                "Shares", "Price/share"] + [p.name for p in s.players.values()])
    for c in s.companies.values():
        w.writerow([c.name, c.status, s.name(c.ceo), c.cash, c.counted_round or "",
                    f"{float(s.company_value(c)):.2f}", c.outstanding,
                    f"{float(s.share_price(c)):.2f}"]
                   + [c.holdings.get(p, 0) for p in s.players])
    w.writerow([])
    w.writerow(["Deeds"])
    w.writerow(["Deed", "Colour", "Owner", "Buildings", "Mortgaged", "Value"])
    for d in s.deeds.values():
        w.writerow([d.name, d.group, s.name(d.owner), d.houses, "Y" if d.mortgaged else "N",
                    f"{float(d.value):.2f}"])
    if s.final_cash is not None:
        w.writerow([])
        w.writerow(["Net worth"])
        w.writerow(["Rank", "Player", "Cash", "Deeds & buildings", "Shares", "Net worth"])
        for i, r in enumerate(s.net_worth(s.final_cash), 1):
            w.writerow([i, r["player"].name, r["cash"], f"{float(r['deeds']):.2f}",
                        f"{float(r['shares']):.2f}", f"{float(r['total']):.2f}"])
    return Response(out.getvalue(), mimetype="text/csv", headers={
        "Content-Disposition": f"attachment; filename=monopoly-plus-{utcnow()[:10]}.csv"})


@bp.post("/import")
def import_json():
    upload = request.files.get("file")
    try:
        data = json.loads(upload.read().decode("utf-8")) if upload else None
        if not data or data.get("format") != "monopoly-plus-events":
            raise RuleError("That isn't a Monopoly+ event export")
        store().import_events(data["events"])
    except (RuleError, ValueError, KeyError) as e:
        flash(f"Import failed: {e}", "error")
        return redirect(url_for("banker.setup"))
    flash("Game imported.", "ok")
    return redirect(url_for("banker.board"))


# --- header actions ----------------------------------------------------------------

@bp.post("/round")
@needs_game
def next_round():
    try:
        target = to_int(request.form.get("round"), "Round") or g.state.round + 1
        _, entries = store().record(("round", {"round": target}))
        flash_entries(entries)
    except RuleError as e:
        flash(str(e), "error")
    return back(url_for("banker.board"))


@bp.post("/undo")
@needs_game
def undo():
    try:
        _, ev = store().undo_last()
        flash(f"Undid #{ev.id} ({ev.type.replace('_', ' ')}).", "ok")
    except RuleError as e:
        flash(str(e), "error")
    return back(url_for("banker.history"))


# --- board -----------------------------------------------------------------------------

@bp.get("/")
@needs_game
def board():
    return render_template("board.html", options=founding_options())


def founding_options():
    return {o.group: o for o in g.state.founding_options()}


def group_response(gid: str, items: list[tuple[str, dict]]):
    """Record board events and re-render the colour group (htmx) or redirect."""
    message = error = None
    try:
        g.state, entries = store().record(*items)
        message = " ".join([e.text for e in entries] + [x for e in entries for x in e.effects])
    except RuleError as e:
        error = str(e)
    if not is_htmx():
        flash(error or message, "error" if error else "ok")
        return redirect(url_for("banker.board") + f"#group-{gid}")
    return render_template("_group.html", gid=gid, options=founding_options(),
                           message=message, error=error, oob=True)


@bp.post("/deeds/<did>/owner")
@needs_game
def deed_owner(did):
    d = g.state.deed(did)
    return group_response(d.group, [("set_owner", {"deed": did,
                                                   "owner": request.form.get("owner") or None})])


@bp.post("/deeds/<did>/mortgage")
@needs_game
def deed_mortgage(did):
    d = g.state.deed(did)
    return group_response(d.group, [("set_mortgage", {"deed": did, "mortgaged": not d.mortgaged})])


@bp.post("/groups/<gid>/houses")
@needs_game
def group_houses(gid):
    try:
        houses = {d.id: to_int(request.form.get(d.id), d.name) or 0 for d in g.state.group_deeds(gid)}
    except RuleError as e:
        return render_template("_group.html", gid=gid, options=founding_options(), error=str(e))
    return group_response(gid, [("set_houses", {"group": gid, "houses": houses})])


# --- companies -----------------------------------------------------------------------

@bp.get("/companies")
@needs_game
def companies():
    return render_template("companies.html")


@bp.get("/companies/<cid>")
@needs_game
def company(cid):
    c = g.state.companies.get(cid)
    if c is None:
        flash(f"No company {cid}", "error")
        return redirect(url_for("banker.companies"))
    return render_template("company.html", c=c, form=MultiDict(), preview=meeting_preview(c, MultiDict()))


@bp.post("/companies/<cid>/count")
@needs_game
def count_envelope(cid):
    try:
        cash = to_int(request.form.get("cash"), "Envelope cash")
        if cash is None:
            raise RuleError("Enter the amount you counted")
        _, entries = store().record(("envelope_count", {"company": cid, "cash": cash}))
        flash_entries(entries)
    except RuleError as e:
        flash(str(e), "error")
    return back(url_for("banker.company", cid=cid))


@bp.post("/companies/<cid>/admin")
@needs_game
def company_admin(cid):
    action = request.form.get("action")
    try:
        if action == "rename":
            items = [("rename_company", {"company": cid, "name": request.form.get("name", "")})]
        elif action == "ceo":
            items = [("set_ceo", {"company": cid, "player": request.form.get("ceo")})]
        else:
            raise RuleError("Unknown action")
        _, entries = store().record(*items)
        flash_entries(entries)
    except RuleError as e:
        flash(str(e), "error")
    return redirect(url_for("banker.company", cid=cid))


def meeting_preview(c, form) -> dict:
    """Vote tally, dividend split, auction opening bid and building costs for the form."""
    s = g.state
    yes = form.getlist("yes")
    out = {"yes": s.yes_shares(c, yes), "passes": s.vote_passes(c, yes),
           "needed": float(s.settings.vote_threshold * c.outstanding), "errors": {}}
    try:
        amount = to_int(form.get("dividend"), "Dividend")
        if amount:
            out["dividend"] = rules.dividend_split(amount, c.holdings)
    except RuleError as e:
        out["errors"]["dividend"] = str(e)
    try:
        n = to_int(form.get("auction_shares"), "New shares")
        if n and n > 0:
            out["auction"] = {"shares": n, "start": s.auction_start_bid(c, n),
                              "after": c.outstanding + n}
    except RuleError as e:
        out["errors"]["auction"] = str(e)
    return out


@bp.post("/companies/<cid>/preview")
@needs_game
def company_preview(cid):
    c = g.state.company(cid)
    return render_template("_meeting_preview.html", c=c, preview=meeting_preview(c, request.form),
                           oob=True)


@bp.post("/companies/<cid>/motion")
@needs_game
def company_motion(cid):
    c = g.state.company(cid)  # inactive companies fall through to rule_error
    f = request.form
    motion = f.get("motion", "")
    yes = f.getlist("yes")
    try:
        if motion == "dividend":
            item = ("dividend", {"company": cid, "amount": to_int(f.get("dividend"), "Dividend"),
                                 "yes": yes})
        elif motion == "auction":
            item = ("share_auction", {"company": cid,
                                      "shares": to_int(f.get("auction_shares"), "New shares"),
                                      "winner": f.get("auction_winner") or None,
                                      "bid": to_int(f.get("auction_bid"), "Winning bid"),
                                      "yes": yes})
            if not item[1]["winner"]:
                raise RuleError("Pick the auction winner. If nobody bid, nothing is sold "
                                "and there is nothing to record.")
        elif motion.startswith("houses:"):
            gid = motion.split(":", 1)[1]
            houses = {d.id: to_int(f.get(f"houses-{d.id}"), d.name) or 0
                      for d in g.state.group_deeds(gid)}
            item = ("set_houses", {"group": gid, "houses": houses, "yes": yes})
        elif motion.startswith(("mortgage:", "unmortgage:")):
            verb, did = motion.split(":", 1)
            item = ("set_mortgage", {"deed": did, "mortgaged": verb == "mortgage", "yes": yes})
        else:
            raise RuleError("Pick a motion")
        _, entries = store().record(item)
    except RuleError as e:
        return render_template("company.html", c=c, form=f, error=str(e),
                               preview=meeting_preview(c, f))
    flash_entries(entries)
    return redirect(url_for("banker.company", cid=cid))


# --- founding ------------------------------------------------------------------------

def founding_form(f):
    gid = f.get("group")
    cash = {k[5:]: to_int(v, "Cash") or 0 for k, v in f.items() if k.startswith("cash-")}
    return gid, cash, f.get("ceo")


@bp.get("/found")
@needs_game
def found():
    gid = request.args.get("group")
    options = founding_options()
    if gid not in options or not options[gid].eligible:
        gid = None
    return render_template("found.html", options=options, gid=gid, form=MultiDict(), preview=None)


@bp.post("/found/preview")
@needs_game
def found_preview():
    preview = preview_error = None
    try:
        gid, cash, ceo = founding_form(request.form)
        if ceo:
            preview = g.state.founding_preview(gid, cash, ceo)
    except RuleError as e:
        preview_error = str(e)
    return render_template("_found_preview.html", preview=preview, preview_error=preview_error)


@bp.post("/found")
@needs_game
def found_post():
    f = request.form
    try:
        gid, cash, ceo = founding_form(f)
        _, entries = store().record(("found", {"company": store().next_id("c"), "group": gid,
                                               "name": f.get("name", ""), "ceo": ceo,
                                               "cash": cash}))
    except RuleError as e:
        preview = None
        try:
            preview = g.state.founding_preview(*founding_form(f))
        except RuleError:
            pass
        return render_template("found.html", options=founding_options(), gid=f.get("group"),
                               form=f, preview=preview, error=str(e))
    flash_entries(entries)
    cid = entries[0].event.payload["company"]
    flash("Hand the founders' cash to the new envelope.", "warn")
    return redirect(url_for("banker.company", cid=cid))


# --- share trades ------------------------------------------------------------------------

@bp.route("/trade", methods=["GET", "POST"])
@needs_game
def trade():
    f = request.form
    if request.method == "POST":
        try:
            _, entries = store().record(("share_transfer", {
                "company": f.get("company"), "from": f.get("from"), "to": f.get("to"),
                "shares": to_int(f.get("shares"), "Shares"),
                "price": to_int(f.get("price"), "Price")}))
            flash_entries(entries)
            return redirect(url_for("banker.trade"))
        except RuleError as e:
            return render_template("trade.html", form=f, error=str(e))
    return render_template("trade.html", form=request.args)


# --- merger --------------------------------------------------------------------------------

def merge_items(f) -> list[tuple[str, dict]]:
    """Envelope counts entered on the merge form, as events to record first."""
    items = []
    for key in ("a", "b"):
        cash = to_int(f.get(f"count_{key}"), "Counted cash")
        if cash is not None and f.get(key):
            items.append(("envelope_count", {"company": f.get(key), "cash": cash}))
    return items


def render_merge(f, template: str, error: str | None = None):
    """Render the merge page or its preview, computed on a scratch state with
    the envelope counts typed into the form already applied."""
    preview = preview_error = None
    scratch = store().state()
    if f.get("a") and f.get("b"):
        try:
            for type_, payload in merge_items(f):
                scratch.apply(Event(None, type_, payload, utcnow()))
            preview = scratch.merger_preview(f.get("a"), f.get("b"), f.get("ceo") or None)
        except RuleError as e:
            preview_error = str(e)
    return render_template(template, form=f, preview=preview, scratch=scratch, error=error,
                           preview_error=preview_error)


@bp.get("/merge")
@needs_game
def merge():
    return render_merge(request.args, "merge.html")


@bp.post("/merge/preview")
@needs_game
def merge_preview():
    return render_merge(request.form, "_merge_preview.html")


@bp.post("/merge")
@needs_game
def merge_post():
    f = request.form
    try:
        items = merge_items(f) + [("merge", {
            "company": store().next_id("c"), "a": f.get("a"), "b": f.get("b"),
            "name": f.get("name", ""), "ceo": f.get("ceo") or None,
            "yes_a": f.getlist("yes_a"), "yes_b": f.getlist("yes_b")})]
        _, entries = store().record(*items)
    except RuleError as e:
        return render_merge(f, "merge.html", error=str(e))
    flash_entries(entries)
    flash("Pour both envelopes into one for the new company.", "warn")
    return redirect(url_for("banker.company", cid=entries[-1].event.payload["company"]))


# --- bankruptcy ----------------------------------------------------------------------------

def parse_creditor(value: str | None) -> dict | None:
    if not value:
        return None
    kind, _, ref = value.partition(":")
    return {"kind": kind, "id": ref or None}


@bp.route("/bankruptcy", methods=["GET", "POST"])
@needs_game
def bankruptcy():
    f = request.form if request.method == "POST" else request.args
    debtor = g.state.players.get(f.get("player") or "")
    creditor = parse_creditor(f.get("creditor"))
    error = None
    if request.method == "POST":
        try:
            if debtor is None or creditor is None:
                raise RuleError("Pick the bankrupt player and the creditor")
            payload = {"player": debtor.id, "creditor": creditor}
            if creditor["kind"] != "player":
                payload["deeds"] = {
                    d.id: {"buyer": f.get(f"deed-{d.id}-buyer") or None,
                           "price": to_int(f.get(f"deed-{d.id}-price"), d.name)}
                    for d in g.state.deeds_of(debtor.id)}
                payload["shares"] = {
                    cid: {"buyer": f.get(f"shares-{cid}-buyer") or None,
                          "price": to_int(f.get(f"shares-{cid}-price"), g.state.name(cid))}
                    for cid in g.state.holdings_of(debtor.id)}
                for sale in list(payload["deeds"].values()) + list(payload["shares"].values()):
                    if sale["buyer"] and sale["price"] is None:
                        raise RuleError("Enter the winning bid for every lot that sold")
                if creditor["kind"] == "company":
                    payload["cash"] = to_int(f.get("cash"), "Cash handed over") or 0
            _, entries = store().record(("bankruptcy", payload))
            flash_entries(entries)
            return redirect(url_for("banker.history"))
        except RuleError as e:
            error = str(e)
    return render_template("bankruptcy.html", form=f, debtor=debtor, creditor=creditor,
                           error=error)


# --- time limit ----------------------------------------------------------------------------

@bp.route("/endgame", methods=["GET", "POST"])
@needs_game
def endgame():
    f = request.form
    if request.method == "POST":
        try:
            items = []
            for c in g.state.active_companies:
                cash = to_int(f.get(f"count-{c.id}"), f"{c.name} envelope")
                if cash is not None:
                    items.append(("envelope_count", {"company": c.id, "cash": cash}))
            items.append(("final_tally", {"cash": {
                p.id: to_int(f.get(f"cash-{p.id}"), f"{p.name}'s cash")
                for p in g.state.active_players}}))
            if any(v is None for v in items[-1][1]["cash"].values()):
                raise RuleError("Enter every player's cash (0 if they have none)")
            g.state, entries = store().record(*items)
            flash_entries(entries)
            return redirect(url_for("banker.endgame"))
        except RuleError as e:
            return render_template("endgame.html", form=f, error=str(e))
    return render_template("endgame.html", form=f)


# --- history ----------------------------------------------------------------------------------

@bp.get("/history")
@needs_game
def history():
    events = store().events()
    entries = {e.event.id: e for e in g.state.log}
    return render_template("history.html", events=list(reversed(events)), entries=entries)


@bp.post("/events/<int:event_id>/<action>")
@needs_game
def event_action(event_id, action):
    if action not in ("void", "restore"):
        return redirect(url_for("banker.history"))
    try:
        store().set_voided(event_id, action == "void")
        flash(f"{'Voided' if action == 'void' else 'Restored'} #{event_id}.", "ok")
    except RuleError as e:
        flash(str(e), "error")
    return redirect(url_for("banker.history"))


# --- public books ----------------------------------------------------------------------------

@bp.get("/books")
def books():
    g.state = store().state()
    return render_template("books.html", live=False)


@bp.get("/books/live")
def books_live():
    g.state = store().state()
    return render_template("_books.html")


@bp.get("/qr")
@needs_game
def qr():
    url = books_url()
    svg = segno.make(url, error="m").svg_inline(scale=8, border=2, dark="#111", light="#fff")
    return render_template("qr.html", url=url, svg=svg,
                           warn_localhost=url.startswith(("http://localhost", "http://127.")))
