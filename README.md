# Monopoly+ banker

A small LAN web app for the banker in **Monopoly+**, a house-rules variant where players form companies
that own colour sets and issue shares. It replaces `Monopoly_Corporations_Banker.xlsx`. The rules are in
[HANDOFF.md](HANDOFF.md).

## Run it

```bash
make run
```

This builds the image and starts it detached on `0.0.0.0:8080`. The banker UI is at
<http://localhost:8080>. Players can scan **Books QR** in the header to open the read-only `/books` page on
their phones (same Wi-Fi). The Makefile passes this machine's LAN IP into the container for that QR code
(`ipconfig getifaddr en0` on macOS, `hostname -I` on Linux); override it with
`make run HOST_IP=192.168.1.50` if it picks the wrong interface.

`make` on its own lists every target. The usual ones:

| Command                  | What it does                                               |
|--------------------------|------------------------------------------------------------|
| `make run`               | Build and start the banker in the background               |
| `make stop`              | Stop and remove the container (the game data stays)        |
| `make restart`           | `stop` then `run`                                          |
| `make ps` / `make logs`  | Container status / follow the server log                   |
| `make install`           | Create `venv/` (Python 3.13 by default) and install deps   |
| `make test`              | Run the tests in the venv                                  |
| `make dev`               | Flask dev server with reload on port 8080, no Docker       |

The game lives in `db/monopoly.db`, a gitignored folder bind-mounted into the container, so it survives
restarts and rebuilds. Starting a new game keeps older games in the file. To back up, copy the file while
the banker is stopped, or use **Setup → Export game**, which downloads the full event log as JSON for
**Import game** to restore. To wipe everything, `make stop` and delete `db/`.

## How it works

Every change is an **event** appended to a log (`found`, `share_transfer`, `dividend`, `merge`,
`bankruptcy`, …). The current state is never stored: each request replays the non-voided events in order.
Each event handler checks the rules against the state so far, so an illegal action is refused before it
is written. **Undo** voids an event and replays the log; a void is refused if a later event would no
longer make sense (e.g. voiding a founding that later share trades depend on). Voided events can be
restored from **History**.

```
app/
  rules.py    pure banker formulas (share split, vote, dividend, auction bid, ...), no state
  state.py    the event handlers and the fold that turns the log into game state
  store.py    SQLite event store; validates, appends, voids, imports and exports
  views.py    Flask routes for the banker screens and /books
  templates/  Jinja pages; htmx swaps fragments for inline board edits and live previews
  static/     CSS, a little JS, and a vendored htmx 2.0.4 (no CDN, works offline)
data/uk.json  UK board: deeds, prices, house costs, colours, default company names
tests/        rules, fold/store, and a full game walked through the web UI
```

The stack is Python, Flask, raw `sqlite3` and Jinja, plus vendored htmx. It has no build step and no ORM.

## Banker conventions the rules leave open

- **Envelope cash** is the last count plus every flow the app records since then: dividends, auction
  proceeds, buildings, mortgages and bankruptcy proceeds. Rent paid in after a count isn't known until the
  next count. Spending more than the tracked amount is refused with a prompt to count the envelope.
- **Rounding** follows the workbook. Prices stay exact (fractions), and payments round half away from zero
  like Excel's `ROUND`. Dividends round down per player. In a 100-share split, the biggest contributor
  absorbs the rounding difference; on a tie for biggest, the CEO absorbs it.
- **Bankruptcy to the bank or a company:** buildings go back to the bank at half price, and that money goes
  to the creditor too. Unsold deeds return to the bank unmortgaged. Unsold shares are cancelled, so the
  other holders' percentages rise.
- **Normal Monopoly money:** mortgaging pays half the printed price, unmortgaging costs that plus 10%, and
  the bank buys buildings back at half cost. Building must be even, and only on an unmortgaged full set.
- The app shows how many houses and hotels the bank has left (32/12), but doesn't block building when it
  runs out.
