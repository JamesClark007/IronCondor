# Runbook: setting up, running continuously, and measuring the 0DTE bot

This picks up where `SETUP_GUIDE.md` leaves off. That guide gets you to a
first successful run. This one covers the two things it doesn't: keeping the
thing running unattended, and knowing whether it's actually any good.

Read "Before you start" first. There are two items there that will bite you
within the first hour if you skip them.

---

## Before you start

**1. The API token you pasted into chat should be treated as burned.**
Anything pasted into a chat window lives in a log somewhere. Go to
<https://tradier.com> → your name → **API Access** → **Sandbox Account
Access**, regenerate the token, and use the new one. It costs you thirty
seconds. The account number (`VA…`) is not secret in the same way, but the
token is a bearer credential: anyone holding it can trade that account.

The new token goes in GitHub Secrets or a `.env` file on a server. It never
goes in a file you commit. Add this to `.gitignore` before your first push:

```gitignore
.env
*.env
__pycache__/
*.pyc
```

**2. `requirements.txt` is missing a dependency.** `filters.py` imports
`numpy`, but the original requirements file only lists `requests` and
`pytz`. Every run would have died at import. The corrected file is included
alongside this runbook:

```text
requests>=2.31
pytz>=2024.1
numpy>=1.26
```

---

## What you're actually setting up

Three independent jobs, sharing one small file:

| Job | When | What it does |
|---|---|---|
| `entry.py` | once, 09:35–09:45 ET | runs the filter gate, and if everything passes, opens one iron condor |
| `manage.py` | every ~5 min, session hours | takes profit at 25%, defends a tested side, flattens before the close |
| `report.py` | once, after the close | records the day's equity and prints realized P&L |

`state.json` is the shared memory between them. It's how a `manage.py` run at
11:20 knows what an `entry.py` run placed at 09:37. If that file is lost or
stale, the bot forgets it has a position.

---

## Stage 1 — Local verification (30 minutes, do not skip)

Debugging on your own machine is far faster than debugging through CI logs.

```bash
cd tradier-0dte-bot
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

export TRADIER_TOKEN=your_new_token
export TRADIER_ACCOUNT_ID=VA63123882
```

Windows PowerShell uses `$env:TRADIER_TOKEN="..."` instead of `export`.

**1a. Confirm the account is reachable.**

```bash
python check_positions.py
```

A balance printout plus "No open positions" is a pass. A 401 means the token
is wrong; a 404 usually means the account ID is wrong or you're pointed at
production with a sandbox token.

**1b. Resolve the VIX symbols.**

```bash
python find_vix_symbols.py
```

Whatever prints ✅ goes to the **front** of the corresponding candidate list
at the top of `filters.py`. If nothing works for `VIX1D`/`VIX9D`, that's
expected and handled: `get_short_vol_stagnation` falls back to an intraday
range, then to VIX's own standard deviation. If nothing works for plain
`VIX` either, the VIX trend filter will fail every single day and the bot
will never enter. That's a hard blocker, not a warning.

**1c. Dry-run the entry path.**

```bash
python entry.py
```

Outside 09:35–09:45 ET this correctly prints "Outside entry window" and
exits. To exercise the mechanics right now, temporarily widen the window in
`entry.py` to `00:00`/`23:59`, run it, then **put it back**. Leaving it wide
means the bot will happily open a condor at 3pm on a Tuesday.

What you want to see: every filter printing PASS or a specific FAIL reason
with real numbers, and, if they all pass, a PREVIEW block with four real OCC
symbols and a positive credit.

**1d. Check the report tooling runs.**

```bash
python report.py
```

It'll be empty this early. You're just confirming it authenticates and the
endpoints respond.

**Checkpoint:** all four scripts run without tracebacks.

---

## Stage 2 — Choose how it runs unattended

There are two honest options, and the difference matters for this strategy.

**Option A — GitHub Actions.** Free, nothing to maintain, but the scheduler
is explicitly best-effort. GitHub documents that scheduled workflows can be
delayed during high load, and delays of 5–15 minutes are common at the top
of the hour — which is exactly when the entry window is. Your 09:35–09:45
entry may simply be missed on busy days, and your 5-minute management loop
may in practice be a 12-minute loop.

**Option B — a small always-on server.** A `$0` Oracle Cloud Always Free VM,
a $5 Hetzner or DigitalOcean box, or a Raspberry Pi in your house. Ordinary
cron on a machine you control fires on time, every time. This is what
"without interruption" actually looks like.

For a strategy whose entire management logic depends on checking a fast-moving
0DTE position on a schedule, Option B is the one I'd run. Option A is a
reasonable place to observe for a couple of weeks first. Both are set out
below; you can start with A and move to B without changing any Python.

---

## Option A — GitHub Actions

### A1. Push the repo

```bash
git init
git add .
git commit -m "0dte bot"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git push -u origin main
```

Make sure `.github/workflows/` actually made it up — hidden directories get
missed surprisingly often. Three workflow files are included with this
runbook: `entry.yml`, `manage.yml`, `report.yml`.

### A2. Secrets and permissions

**Settings → Secrets and variables → Actions → New repository secret:**

- `TRADIER_TOKEN` — your regenerated sandbox token
- `TRADIER_ACCOUNT_ID` — `VA63123882`

**Settings → Actions → General → Workflow permissions:** select **Read and
write permissions**. Without this the workflows can't commit `state.json`
back, and every `manage.py` run will believe the bot is flat.

### A3. Cost and the free-minutes ceiling

This is the detail that quietly kills unattended GitHub bots. The management
loop is roughly 100 billable runner-minutes per trading day, or about
2,200 per month. **Private repositories get 2,000 free minutes a month.**
You will run out around the third week and the workflows will stop with a
billing error.

Three ways out, in order of preference:

1. **Make the repo public.** Public repos get unlimited free Actions
   minutes. Your secrets stay secret — they are not exposed by making the
   repo public. Just make certain no token was ever committed; if one was,
   rewriting history is not enough, you must regenerate it.
2. Drop the management loop to `*/10` in `manage.yml`, roughly halving the
   cost. You are also halving your reaction speed.
3. Move to Option B.

### A4. Timezone handling

GitHub cron is UTC and does not follow daylight saving. The workflows
schedule both the EDT hour and the EST hour, and the Python gates itself on
`America/New_York`, so the wrong one is a harmless no-op. Don't "simplify"
this by deleting one of the cron lines — the bot will go silent in March and
November.

### A5. Manual first run

Actions tab → **0dte-entry** → **Run workflow**. Watch the log. Outside the
entry window it prints "Outside entry window" and exits, which is correct
behaviour, not a failure.

Each run appends its output to the run **Summary** page, so you can read
what happened without opening the raw log.

### A6. Keeping it alive

GitHub disables scheduled workflows in repositories with no activity for 60
days, and commits pushed by the Actions bot do not reliably reset that
clock. Two cheap defences: keep Actions notification emails on (GitHub warns
before disabling), and push a trivial commit or trigger a manual run once a
month. If the schedule ever goes quiet, check **Settings → Actions →
General** first.

---

## Option B — always-on server (recommended for real unattended use)

### B1. Set it up

```bash
sudo adduser --system --group --home /opt/0dte 0dte
sudo -u 0dte -H bash
cd /opt/0dte
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git bot
cd bot
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
mkdir -p logs
```

Credentials go in `/opt/0dte/bot/.env`, readable only by the service user:

```bash
cat > /opt/0dte/bot/.env <<'EOF'
TRADIER_TOKEN=your_new_token
TRADIER_ACCOUNT_ID=VA63123882
EOF
chmod 600 /opt/0dte/bot/.env
```

### B2. The wrapper

Save as `/opt/0dte/bot/run.sh`, then `chmod +x run.sh`. The `flock` is not
optional — it's what stops two overlapping runs from opening two positions.

```bash
#!/usr/bin/env bash
set -euo pipefail
cd /opt/0dte/bot
set -a; source .env; set +a
exec flock -n "/tmp/0dte-$1.lock" \
  ./venv/bin/python "$1.py" "${@:2}" \
  >> "logs/$1-$(date +%F).log" 2>&1
```

### B3. The schedule

`crontab -e` as the service user. `CRON_TZ` makes cron handle daylight
saving for you, which is the main reason to prefer this over UTC arithmetic:

```cron
CRON_TZ=America/New_York

# Entry: every 2 minutes inside the window. entry.py self-gates on the
# clock and on state.json, so extra attempts are no-ops once it has acted.
35-45/2 9 * * 1-5   /opt/0dte/bot/run.sh entry

# Management: every minute of the session. Cheap, and much tighter than
# the 5-minute floor GitHub imposes.
30-59 9 * * 1-5     /opt/0dte/bot/run.sh manage
* 10-15 * * 1-5     /opt/0dte/bot/run.sh manage
0-5 16 * * 1-5      /opt/0dte/bot/run.sh manage

# Daily performance snapshot, 20 minutes after the close.
20 16 * * 1-5       /opt/0dte/bot/run.sh report --days 90 --snapshot

# Weekly log cleanup.
0 3 * * 6           find /opt/0dte/bot/logs -name '*.log' -mtime +45 -delete
```

Once a minute is comfortably inside Tradier's sandbox rate limit of 60
requests per minute — a management pass makes five calls at most, and only
when a position is actually open.

### B4. Watching it

```bash
tail -f /opt/0dte/bot/logs/manage-$(date +%F).log
grep -h "PROFIT TARGET\|tested\|flatten\|Order submitted" logs/*.log
```

On this setup `state.json` lives on the server and is never committed, so
the git-conflict problem the Actions version has to work around doesn't
exist. Back it up if you care about it, but it self-heals every morning.

---

## Stage 3 — Going live on paper

Leave `dry_run = True` for at least a week of real sessions. You are looking
for: filters producing sensible, varying results rather than the same error
every day; strikes that sit somewhere plausible against the expected move;
and preview credits that aren't absurd.

When you're satisfied:

1. `entry.py` → `"dry_run": False`
2. `manage.py` → `"dry_run": False`
3. Commit, push, redeploy.

This is the step where paper orders start actually hitting the account.

---

## Checking performance

### The daily one-liner

```bash
python report.py
```

`report.py` builds everything from Tradier's own records rather than the
bot's logs, so it can't flatter itself. It prints:

- **Account** — equity, cash, open P&L, today's closed P&L
- **Open positions** — what's live right now
- **Closed trades** — one row per close date, since a 0DTE condor is one
  trade per day, with a running total
- **Summary** — trade count, win rate, net P&L, average win, average loss,
  profit factor, max drawdown on the closed-trade curve
- **Cross-check** — net cash from today's filled bot legs, reconstructed
  independently from the order feed

Useful variants:

```bash
python report.py --days 90          # longer window
python report.py --orders           # dump raw orders, incl. rejection reasons
python report.py --days 90 --snapshot
```

### The equity curve

`--snapshot` appends (or updates) one row per day in `equity_curve.csv`:
date, total equity, cash, market value, open position count. The report
workflow and the cron entry above both run it automatically, so after a
month you have a real curve you can open in any spreadsheet. There is no way
to backfill this, so turn it on from day one.

### What to look at, and what to ignore

Iron condors win most of the time by construction. A 78% win rate tells you
close to nothing. The numbers that carry information:

- **Average loss ÷ average win.** For a 10-point wing collecting well under
  a point, a full loss dwarfs many wins. Two or three bad days can erase a
  quarter.
- **Max drawdown.** The only line that tells you what a bad stretch feels
  like.
- **Profit factor.** Below 1.0 means the strategy loses money regardless of
  how good the win rate looks.
- **Entry frequency.** If the filter gate passes 95% of days it isn't
  filtering; if it passes 5% you have too few samples to conclude anything.
  Count "FILTERS FAILED" lines against total runs.
- **Slippage.** Compare the credit `entry.py` predicted from mid prices with
  the actual `avg_fill_price` in `--orders`. On sandbox data that gap is the
  single most optimistic number in the whole system.

### Position and order truth

```bash
python check_positions.py
```

Use this whenever `report.py` and your expectations disagree. You can also
look at the same account in Tradier's web UI, which is a useful third
opinion when the API and `state.json` tell different stories.

### If you're on GitHub Actions

Actions tab → any run → **Summary** shows that run's full output without
opening the log. The daily `0dte-report` run is the one to check first.

---

## Fixes applied to `manage.py`

A patched `manage.py` is included. Diff it against yours before swapping it
in. Four changes:

1. **Side-aware pricing and closing.** The original kept pricing all four
   legs after a defensive close, and would have submitted close orders for
   legs the account no longer held — those get rejected by the broker. It
   now prices and closes only what's still open.
2. **Honest profit accounting after a partial close.** What was paid to
   defend a side is stored in `position["paid_to_close"]` and subtracted,
   so the 25% target measures against the real remaining credit.
3. **End-of-session flatten at 15:45 ET.** The original had no exit for a
   position that never hit its target and was never tested, leaving 0DTE
   contracts to expire. In-the-money expiry means assignment on SPY shares,
   which is not a scenario this bot is built to handle.
4. **A loud warning on stale state.** `new_day_reset_if_needed` silently
   wipes a position left open from a previous day. It now says so first.

Adjust `flatten_after` if you want to sit closer to the bell. Earlier is
safer; later collects more theta.

---

## Things that will go wrong

**Sandbox data is delayed 15 minutes.** Tradier applies the standard delay
to all sandbox data, and there's no way to turn it off. Every fill, every
management decision, every P&L number is struck against 15-minute-old
prices. For a strategy measured in intraday minutes, this is the single
largest gap between these results and live ones. Paper results here are a
test of the plumbing, not a forecast of the edge.

**Greeks may be missing from the sandbox chain.** `get_forward_iv` needs
`mid_iv` or `smv_vol` in the chain response. If you see "No valid IV in ATM
chain greeks" every day, the VRP filter can never pass and the bot will
never trade. The fix is to fall back to the ATM straddle approximation the
original yfinance checker used.

**Entry isn't fully idempotent.** `entry.py` writes `state.json` only after
the order call returns. If the order is accepted but the response handling
throws, the state never records it and the next scheduled attempt can open a
second condor. The `flock` in Option B's wrapper prevents overlap, but not
this. If you want belt and braces, have `entry.py` check
`client.get_positions()` for existing option legs before building an order.

**Market holidays.** Nothing here knows the market calendar. On Thanksgiving
the workflows fire, the filters fail or the chain comes back empty, and the
bot exits. Harmless, but don't read those logs as breakage.

**Short weeks and half days.** A 13:00 ET close means the 15:45 flatten
never runs. Check the calendar around Thanksgiving, Christmas Eve and
July 3rd, or add the half-day dates to `econ_events.csv` so the news filter
blocks entry entirely.

**One position, one file.** `state.json` holds exactly one position on one
symbol. Extending to multiple underlyings means replacing it, not adding
keys.

---

## Weekly maintenance

- Add the coming week's FOMC/CPI/NFP/PCE dates to `econ_events.csv`.
- Run `python report.py --days 30` and look at average loss and drawdown.
- Confirm `equity_curve.csv` gained a row for every trading day. A gap means
  the report job didn't run.
- Skim for "WARNING: state.json carried an OPEN position" — that means a
  position went unmanaged.
- Monthly: confirm the schedule is still enabled, and check your Actions
  minutes if the repo is private.

## Kill switch

Fastest to slowest:

1. Set `"dry_run": True` in both files and redeploy. Everything keeps
   logging, nothing gets submitted.
2. Actions tab → workflow → **⋯** → **Disable workflow**. Or on a server,
   comment out the cron lines.
3. Regenerate the Tradier token without updating the secret. Every call
   fails with a 401 immediately.

Whichever you use, check `check_positions.py` afterwards. Stopping the bot
does not close anything it already opened.
