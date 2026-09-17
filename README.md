# 0DTE Iron Condor — Paper Trading Bot (Tradier sandbox + GitHub Actions)

Automates entry and management of a 0DTE SPY iron condor against **Tradier's
sandbox (paper trading) API**, scheduled for free via **GitHub Actions**.

## What this does

- `filters.py` — the entry gate: variance risk premium (real forward IV from
  the live chain's greeks vs. realized vol on the underlying), VIX trend,
  VIX1D/VIX9D short-vol stagnation (with fallbacks), and a news-day check
  against a CSV you maintain.
- `entry.py` — runs once daily, ~9:33-9:47 AM ET. Runs the full filter gate
  first; if anything fails, it logs why and exits without placing an order.
  If everything passes, it pulls the live SPY chain, places short strikes
  just past the day's expected move, buys wings, and submits (or previews)
  a 4-leg credit order.
- `manage.py` — runs every ~5 minutes during market hours. Closes the full
  position at 25% of max credit captured, or defends a tested short strike
  by closing just that side once its cost-to-close reaches the total net
  credit received (mirroring the breakeven-management rule you described).
- `state.json` — tracks whether a position is open today, committed back to
  the repo by each workflow run so the next run "remembers" it.
- `econ_events.csv` — auto-created on first `entry.py` run; add your own
  FOMC/CPI/NFP/PCE dates here weekly, same as the standalone checker.

## Setup

1. **Create a Tradier developer account and sandbox app**
   https://developer.tradier.com — generates a sandbox API token and a paper
   account number (e.g. `VA00000000`).

2. **Create a GitHub repo and push this folder to it.**

3. **Add two repository secrets** (Settings → Secrets and variables → Actions):
   - `TRADIER_TOKEN` — your sandbox access token
   - `TRADIER_ACCOUNT_ID` — your sandbox account number

4. **Confirm Actions are enabled** for the repo (Settings → Actions →
   General). Scheduled workflows are disabled automatically if a repo goes
   60+ days with no activity — a `workflow_dispatch` manual run or any push
   resets that clock.

5. **Find the real VIX/VIX1D/VIX9D ticker symbols before relying on them.**
   Run `python find_vix_symbols.py` locally (with `TRADIER_TOKEN` and
   `TRADIER_ACCOUNT_ID` set) — it searches Tradier's Market Search and
   Lookup Symbols endpoints for VIX-related tickers, then tries direct
   quotes on the common candidate formats (`VIX`, `$VIX`, `VIX.X`, etc.) and
   tells you which ones actually return data. Put whatever works first in
   the candidate lists at the top of `filters.py`.

6. **Leave `dry_run = True` in both `entry.py` and `manage.py` at first.**
   This previews orders (Tradier validates and returns estimated cost) without
   submitting anything. Watch the Actions logs for a few days, confirm the
   strikes/credit/timing look right, *then* flip `dry_run = False` in both
   files to start actually submitting paper orders.

## Honest limitations — read before trusting this

- **VIX/VIX1D/VIX9D symbol format on Tradier is unconfirmed.** `filters.py`
  tries a couple of common variants (`VIX`, `$VIX`, `VIX.X`, etc.) and uses
  whichever returns real data. Once you have sandbox access, check the entry
  run's log output for which symbol actually worked — adjust the candidate
  lists in `filters.py` if none of them do.
- **GitHub Actions scheduled runs are not guaranteed to the minute.** GitHub
  explicitly documents that scheduled workflows can be delayed, especially
  under high platform load, and the shortest realistic reliable interval is
  about 5 minutes. This is a real gap versus a platform actively streaming
  quotes — a fast move between checks could pass through where the 25%
  target or defense rule would have caught it on a continuously-watched
  screen.
- **Sandbox market data can be delayed**, same caveat as everything else
  we've discussed with free/lower-tier data.
- **State is a single JSON file, not a database.** Fine for one position at a
  time on one symbol; if you extend this to multiple underlyings or multiple
  concurrent positions, replace it with something more robust.
- **This is still paper trading.** Nothing here places real orders — but
  it's worth treating the codebase with the same care you would live code,
  since the goal is presumably to eventually trust it with real money.

## Extending this later

- Verify the VIX symbol candidates against your real sandbox account and
  trim/adjust `filters.py` once you know what works.
- Add a notification step (Slack/Discord webhook, email) so you don't have
  to check Actions logs manually.
- If you outgrow GitHub Actions' polling cadence, this same client code
  drops into a persistent process on a real VPS (e.g. Oracle Cloud's
  Always Free tier) for tighter, near-real-time monitoring instead of
  5-minute polling.
