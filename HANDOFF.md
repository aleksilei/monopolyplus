---
tags: [agent, handoff, monopoly-plus, board-game, web-app]
---
# Handoff: Monopoly+ banker web app

_Updated 2026-10-23, from a Claude chat (claude.ai Project "Monopoly+")._

## Goal
Build a small local web app that the banker (Aleksi) runs during "Monopoly+" games. It replaces the Excel workbook `Monopoly_Corporations_Banker.xlsx`. Monopoly+ is a house-rules variant where players form companies that own colour sets and issue shares.
Done means: a GitHub repo with a `Dockerfile` and a compose project, where `make run` starts the web UI on port `8080`. It must be reachable from phones on the LAN, implement every rule below, and be usable for a real game.

## Done so far
- Rules designed and iterated with the user. The final player rules are below and are the source of truth.
- Excel banker workbook `Monopoly_Corporations_Banker.xlsx` built and tested. It sits next to this file and is a working reference for the calculations. Tabs: Rules, Banker Guide, Settings, Players, Properties, Corporations, Holdings, Share Register, Founding, Board Meeting, Merger, Net Worth.
- Agreed that the app is reasonable and better than the workbook. The workbook mixes current state with history, and an append-only event log fixes that.

## Current state
No code exists yet. The rules are settled and the stack is agreed in outline. Three implementation choices are still open (see Open questions).

## Agreed design constraints
- **Stack:** Python + SQLite + vanilla HTML/CSS/JS, with no React (no justification for it). FastAPI or Flask with Jinja templates and raw `sqlite3`, no ORM. htmx was proposed as an optional single `<script>` tag with no build step; not yet decided.
- **Runtime:** single-service `docker compose`, SQLite file on a volume, `make run` → `docker compose up --build`, published on `0.0.0.0:8080`.
- **Scope:** one active game, LAN only, **no auth**. Players are trusted, and the banker is always the user or a family member who knows some dev stuff.
- **Data model:** an append-only event log (`found`, `share_transfer`, `share_auction`, `envelope_count`, `set_owner`, `set_houses`, `set_mortgage`, `dividend`, `merge`, `close`, `bankruptcy`, …). Current state is derived by folding events. Undo = void events.
- **Rules engine:** pure Python functions with pytest tests: founding validity, 100-share split by value, vote pass (> 50% of all shares), dividend split, auction start bid, merger split, go-private detection, net worth.
- **Board data:** UK edition, loaded from a JSON data file. Colour groups: BROWN, LTBLUE, PINK, ORANGE, RED, YELLOW, GREEN, DKBLUE, RAIL (stations, company name "British Railways"), UTIL. Prices and house costs are standard UK edition, in £. Mortgage value = ½ printed price.
- **Read-only `/books` page:** optional, mobile-friendly, and auto-refreshing. Lists each company's envelope cash with when it was last counted, value, share price, and who owns its shares. The banker UI shows a "Books QR" button that encodes `http://<host-LAN-IP>:8080/books`. Use `segno` for SVG output. Inside Docker the host LAN IP is unknown: pass it in from the Makefile (`ipconfig getifaddr en0` on macOS, `hostname -I` on Linux), or derive it from the request `Host` header. Phones are optional; the banker mostly tells players things verbally.
- **Player cash is secret** and never stored during the game. It is entered only at the time limit, for net worth. Company envelopes are counted only when a price is needed or a player asks for the books. Store "last counted (round)" per company.

## Player rules (final, source of truth)
Normal Monopoly rules (UK edition) apply, plus the rules below. The banker does all the maths.
1. **Starting a company:** when 2 or more players together own a whole colour set, they can turn it into a company. Everyone who owns a deed of that colour must agree and join, and each puts in at least one deed. A player who owns a whole set alone can't start one.
2. Founders put in their deeds, plus any cash they like. The banker gives out 100 shares, split by how much each player put in.
3. The company owns the deeds, and its money lives in its own envelope. Everyone who lands on its streets pays full rent into the envelope, shareholders included.
4. A company can only spend what's in its envelope. It never pays rent or tax and can never go into debt, so companies can't go bankrupt.
5. **Open books:** any player can ask the banker, at any time, to see a company's books (cash, value, share price, shareholders). The banker counts the envelope if needed. Players' own cash stays secret until the time limit.
6. **CEO:** the player who suggested the company. The CEO's only job is that board meetings happen when they pass GO. The CEO can't be voted out. If they go bankrupt, the biggest shareholder takes over (tie: roll dice).
7. **Board meeting:** held every time the CEO passes GO. Shareholders vote on dividends, building or selling houses, mortgaging or unmortgaging, selling new shares, and merging. 1 share = 1 vote, and a motion passes when more than half of all shares vote yes. Being in jail doesn't stop a player attending or voting.
8. **Extra meeting:** a player with ≥ 25% of a company's shares can call one extra meeting per round, on their own turn. This exists for when the CEO is stuck in jail.
9. **Dividends:** the board picks an amount from the envelope, shared out by shares owned.
10. **New shares:** the board picks how many. They're auctioned to all players as one bundle, starting at their current value (bundle × price). The money goes into the envelope. If nobody bids, nothing is sold.
11. Players can trade shares with each other for any price, like deeds. There are no company buybacks; the share count only drops on merge or close.
12. **Merging:** if both boards vote yes, the two companies combine into a NEW company (there is no "survivor"). Everything from both goes in, and 100 new shares are split by the value of each player's old shares, the same as founding. The biggest shareholder becomes CEO (tie: roll dice). Players pick a new name.
13. **Going private:** if one player ends up owning every share, the company closes and that player gets all its deeds, houses and money. The colour can be founded again later.
14. **Going bankrupt:** if the creditor is a player, that player gets everything, shares included (normal Monopoly). If the creditor is the bank or a company, everything the bankrupt player owns is auctioned immediately. Mortgaged deeds sell as mortgaged. The money goes to the bank or into the company's envelope. A company never takes deeds directly.
15. **Winning:** last player standing. If the time limit is reached first (default 180 min), the richest player wins, counting cash + deeds + houses + shares.

## Banker formulas
- **Company value** = envelope cash + deeds at printed price (mortgage value if mortgaged) + buildings at house cost each (hotel = 5). Share price = value ÷ shares outstanding.
- **100-share split** (founding and merger): each player gets `round(100 × their value ÷ total value)`. The biggest contributor absorbs the rounding difference. For a merger, each player's value is `old A shares × A price + old B shares × B price`.
- **Rounding:** prices are kept exact. Every payment is rounded to the nearest £1. Dividends round down per player, and the remainder stays in the envelope.
- **Settings** (editable): starting cash £1,500, 100 founding shares, extra-meeting threshold 25%, vote threshold > 50%, auction start = 100% of value, time cap 180 min.
- **Net worth:** cash + deeds and buildings at value + shares × price.

## Next steps
1. Settle the open questions with the user.
2. Scaffold the repo: `app/` (server, templates, static), `data/uk.json`, `tests/`, `Dockerfile`, `compose.yaml`, `Makefile` (`run`, `test`, `down`), and `README.md`.
3. Implement the event schema and the state fold, then the rules engine with pytest. Use the Excel workbook's worked examples as test cases (see Pointers).
4. Banker screens:
   - Setup (players, settings)
   - Board grid (owner, houses, mortgage inline)
   - Companies (books + count envelope)
   - Found company (wizard with validation)
   - Meeting (votes, dividend split, share-bundle auction)
   - Trade shares
   - Merge
   - Bankruptcy
   - Time limit (net worth)
5. `/books` read-only page and the QR button.
6. Undo, plus an event history view.
7. Optional: export the final state to CSV/XLSX.

## Open questions (user to answer)
- htmx or pure vanilla JS?
- Undo scope: only the last event, or void any past event (state recomputes either way)?
- Keep the Excel workbook as an export format, or retire it?
- Later feature, explicitly parked: government bonds (a new event type plus a coupon paid at the CEO's GO meeting).

## Pointers
- Reference workbook: `Monopoly_Corporations_Banker.xlsx`, next to this file.
- Test cases from the workbook:
  - **Founding:** Bow St + Marlborough St + £100 (P1) and Vine St (P2) → £460 / £200 → 70 / 30 shares at £6.60.
  - **Merger:** Orange £900 / 100 shares (P1 40, P2 60) + Red £1,500 / 100 shares (P2 30, P3 70) → new £2,400 at £24/share → P1 15, P2 41, P3 44. P3 becomes CEO.
- The user's SilverBullet notes vault (`agents/handoffs/monopoly-plus-banker-app.md`) was down when this was written. Copy this handoff there once the home server is back.
