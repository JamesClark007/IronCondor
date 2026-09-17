# Connect and run: zero to a live paper bot

A linear path. Do the steps in order, don't skip a checkpoint. Where this
overlaps `RUNBOOK.md`, that file has the reasoning and this one has the
commands.

Budget about an hour for steps 1–9, then a week of watching before step 10.

---

## Part 0 — How the pieces connect

Nothing here talks to anything else directly. Everything routes through two
credentials and one small file:

```
  TRADIER_TOKEN + TRADIER_ACCOUNT_ID   (environment variables)
                  │
                  ▼
          tradier_client.py            every HTTP call to Tradier
           │       │       │
    ┌──────┘       │       └──────────────┐
    ▼              ▼                      ▼
 filters.py     entry.py               manage.py            report.py
 (gate)         09:35-09:45            every ~5 min         after close
                    │                      ▲  │                  │
                    │  writes              │  │ writes           │ writes
                    └──────────►  state.json  ◄┘                 ▼
                                    │                     equity_curve.csv
                                    │
                             econ_events.csv  (you maintain this)
```

Three consequences worth internalising before you start:

- **The credentials are the only wiring.** There is no config file with a
  broker in it. If `TRADIER_TOKEN` and `TRADIER_ACCOUNT_ID` are visible to
  the process, it's connected. If not, nothing works.
- **`state.json` is the only memory.** `entry.py` writes it, `manage.py`
  reads it. If it's missing, stale, or not shared between the two jobs, the
  bot opens a position and then forgets it exists.
- **`econ_events.csv` is the only human input.** It's created empty on the
  first `entry.py` run and does nothing until you put dates in it.

---

## Part 1 — Assemble the files

### Step 1. Unpack the folder

Unzip `tradier-0dte-bot.zip` into wherever you keep projects. It's
self-contained — everything below is already inside it, nothing needs to be
copied in from elsewhere.

```bash
unzip tradier-0dte-bot.zip
cd tradier-0dte-bot
```

```
tradier-0dte-bot/
├── .github/
│   └── workflows/
│       ├── entry.yml          ← NEW   daily entry schedule
│       ├── manage.yml         ← NEW   5-minute management loop
│       └── report.yml         ← NEW   post-close performance snapshot
├── .env.example               ← NEW   template for server credentials
├── .gitignore                 ← NEW   keeps .env out of git
├── CONNECT_AND_RUN.md         ← this file
├── RUNBOOK.md                 ← NEW   operations and performance reference
├── README.md                  unchanged
├── SETUP_GUIDE.md             unchanged
├── check_positions.py         unchanged
├── econ_events.csv            ← NEW   news-day list, you maintain it
├── entry.py                   unchanged
├── filters.py                 unchanged (you edit the VIX lists in step 7)
├── find_vix_symbols.py        unchanged
├── manage.py                  ← REPLACED (diff it first)
├── report.py                  ← NEW   performance reporting
├── requirements.txt           ← REPLACED (adds numpy)
├── run.sh                     ← NEW   cron wrapper for Option B
├── state_store.py             unchanged
└── tradier_client.py          unchanged
```

Two files appear on their own once the bot runs and are not shipped:
`state.json` (written by `entry.py`) and `equity_curve.csv` (written by
`report.py --snapshot`).

If you're on a server, `chmod +x run.sh` after unpacking — file permissions
don't always survive a download.

The `manage.py` inside the zip already has the fixes applied — you don't
need to merge anything. If you want to see exactly what changed from a
stock version of this bot, the four differences are listed in `RUNBOOK.md`
under "Fixes applied"; the one to actually read is the end-of-session
flatten, since without it a position that never hits target or gets tested
just rides into expiry.

### Step 2. Confirm `.gitignore` is in place

It ships with the package, but check it survived the copy **before** your
first `git add`. Once a secret is committed, deleting it later doesn't help;
you have to regenerate it.

```bash
cat .gitignore
```

You should see `.env` on the first line. Note that `state.json` and
`equity_curve.csv` are deliberately **not** ignored — on GitHub Actions they
have to be committed, since that's how state survives between runs.

### Step 3. Regenerate the token

Go to <https://tradier.com> → your name → **API Access** → **Sandbox Account
Access** → regenerate. The old one was pasted into a chat window; treat it as
public. Keep the new token somewhere you can paste from twice.

Your account number is `VA63123882`. That one doesn't change.

**Checkpoint:** the file tree above, a `.gitignore`, and a fresh token.

---

## Part 2 — Connect locally and prove it works

### Step 4. Environment

```bash
cd tradier-0dte-bot
python3 -m venv venv
source venv/bin/activate              # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

`pip list | grep -i numpy` should show numpy. If it doesn't, you're still on
the old requirements file and every run will die at import.

### Step 5. Credentials

```bash
export TRADIER_TOKEN=your_new_token_here
export TRADIER_ACCOUNT_ID=VA63123882
```

PowerShell:

```powershell
$env:TRADIER_TOKEN="your_new_token_here"
$env:TRADIER_ACCOUNT_ID="VA63123882"
```

These live only in this shell. Open a new terminal and you'll have to set
them again — that's the usual cause of a sudden "Missing TRADIER_TOKEN"
error halfway through a session.

### Step 6. Prove the connection

```bash
python check_positions.py
```

Expected: a balance block, then "No open positions" and "No orders found".

If it fails, stop here. Nothing downstream can work:

| What you see | What it means |
|---|---|
| `Missing TRADIER_TOKEN or TRADIER_ACCOUNT_ID` | step 5 didn't take, or you changed terminals |
| `401 Unauthorized` | wrong token, or a production token against sandbox |
| `404 Not Found` | wrong account number |
| `403` / HTML in the error | you're hitting production URLs; confirm `sandbox=True` in `TradierClient` |

**Checkpoint:** a real balance printed from your account.

### Step 7. Resolve the VIX symbols

```bash
python find_vix_symbols.py
```

Every ✅ moves to the **front** of the matching list at the top of
`filters.py`:

```python
VIX_SYMBOL_CANDIDATES   = ["VIX", "$VIX", "VIX.X"]
VIX1D_SYMBOL_CANDIDATES = ["VIX1D", "$VIX1D"]
VIX9D_SYMBOL_CANDIDATES = ["VIX9D", "$VIX9D"]
```

No ✅ for VIX1D or VIX9D is fine — `filters.py` falls back to an intraday
range, then to VIX's own standard deviation. **No ✅ for plain VIX at all is
a blocker**: the VIX trend filter will fail every day and the bot will never
enter. If that happens, either find a working index symbol or drop that
filter deliberately rather than letting it silently veto everything.

### Step 8. Dry-run the entry path

The real window is 09:35–09:45 ET. To test outside it, widen the window
temporarily in `entry.py`:

```python
"entry_window_start": "00:00",
"entry_window_end": "23:59",
```

```bash
python entry.py
```

Read the output line by line. You want a PASS or a specific FAIL for each of:
news day, realized vol, forward IV, VRP ratio, VIX trend, short-vol
stagnation. Then, if all passed, four OCC symbols and a PREVIEW block.

**Put the window back to `09:35` / `09:45` now.** Left wide, the bot will
open a condor at 3pm on a Tuesday.

Two failures worth recognising immediately:

- `No valid IV in ATM chain greeks` — the sandbox isn't returning greeks.
  The VRP filter can never pass. See `RUNBOOK.md`, "Things that will go
  wrong".
- `No working VIX symbol found` — go back to step 7.

`econ_events.csv` appears after this run. Open it and add the coming
month's FOMC, CPI, NFP and PCE dates:

```csv
date,event
2026-09-18,FOMC
2026-10-02,NFP
```

### Step 9. Dry-run the management path

`manage.py` does nothing while `state.json` says flat, so to exercise it you
need a position. Easiest honest way: wait until step 11 produces one. If you
want to test the logic now, hand-write a `state.json` with today's date and
four real OCC symbols from the chain, run `python manage.py`, then delete it.

```bash
python report.py
```

This should authenticate and print an empty report. You're confirming the
gain/loss and history endpoints respond on your account, not looking at
numbers yet.

**Checkpoint:** filters produce real numbers, a preview order appears, and
`report.py` runs clean.

---

## Part 3 — Connect the scheduler

Pick one. `RUNBOOK.md` Part "Stage 2" explains the tradeoff; the short
version is that GitHub's scheduler is best-effort and routinely runs 5–15
minutes late, which matters for a ten-minute entry window.

### Option A — GitHub Actions

```bash
git init
git add .
git commit -m "0dte bot"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git push -u origin main
```

Confirm the workflows made it up — hidden directories get dropped often:

```bash
git ls-files .github
```

Then in the repo settings:

1. **Settings → Secrets and variables → Actions → New repository secret**
   - `TRADIER_TOKEN` = your new token
   - `TRADIER_ACCOUNT_ID` = `VA63123882`
2. **Settings → Actions → General → Workflow permissions** → **Read and
   write permissions**. Without this, `state.json` never gets committed back
   and `manage.py` will believe the bot is flat all day.
3. If the repo is private, read "Cost and the free-minutes ceiling" in the
   runbook before you leave it running. You will hit the 2,000-minute cap
   around week three.

Smoke test it by hand: **Actions → 0dte-entry → Run workflow**. Outside the
window it prints "Outside entry window" and exits, which is a pass. Check
the run's **Summary** tab — the workflows publish their output there so you
don't have to dig through raw logs.

### Option B — always-on server

Full setup is in `RUNBOOK.md` under "Option B". The connection-specific
parts:

```bash
# on the server, as the service user
cat > /opt/0dte/bot/.env <<'EOF'
TRADIER_TOKEN=your_new_token_here
TRADIER_ACCOUNT_ID=VA63123882
EOF
chmod 600 /opt/0dte/bot/.env
```

`run.sh` sources that file, so cron jobs inherit the credentials without
them ever appearing in the crontab or a process list. Test the wiring before
trusting the schedule:

```bash
/opt/0dte/bot/run.sh check_positions
cat /opt/0dte/bot/logs/check_positions-$(date +%F).log
```

Then install the crontab from the runbook and confirm with `crontab -l`.

**Checkpoint:** a manually triggered run reaches your account and logs a
balance.

---

## Part 4 — Run it

### Step 10. A week in dry-run

Leave `dry_run = True` in both `entry.py` and `manage.py`. Let the schedule
run untouched for five real sessions.

Each day, check one thing: did the filter gate produce a *varying, sensible*
answer? A different reason each day is healthy. The same error every day
means a filter is broken, not selective — and a broken filter that always
fails looks identical to a strategy that's being appropriately picky.

### Step 11. Turn on submission

```python
# entry.py
"dry_run": False,
# manage.py
"dry_run": False,
```

Commit and push (Actions), or `git pull` on the server. This is the step
where orders actually reach the account.

### Step 12. The first live day, minute by minute

| Time (ET) | What should happen | Where to look |
|---|---|---|
| 09:35–09:45 | entry runs, filters print, order submitted or a stated reason not to | entry log / Actions summary |
| 09:45 | `state.json` shows `"status": "open"` with four symbols | repo file, or server |
| ~09:50 | four legs visible on the account | `python check_positions.py` |
| all session | management logs "No action needed" or acts | manage log |
| whenever | profit target, defensive close, or 15:45 flatten | look for `Order submitted` |
| 16:00 | flat again, `state.json` says `closed` | `check_positions.py` |
| 16:20 | daily report and equity snapshot | `equity_curve.csv` |

The one mismatch that matters: `state.json` says open, the account says
flat, or vice versa. That means the state file isn't being shared or
committed correctly, and every management decision after it is being made
against a fiction. Fix that before the next session.

### Step 13. Reading performance

```bash
python report.py --days 30
```

Ignore win rate — condors win by construction. Look at average loss against
average win, max drawdown, and how often the gate actually let a trade
through. `RUNBOOK.md`, "What to look at, and what to ignore", goes into
this properly.

---

## Quick reference

```bash
# Is it connected?
python check_positions.py

# What did it do, and was it any good?
python report.py --days 30
python report.py --orders            # raw orders + rejection reasons

# What does it think it's holding?
cat state.json

# Stop it now
#   1. dry_run = True in both files, redeploy
#   2. disable the workflow / comment the cron lines
#   3. regenerate the Tradier token without updating the secret
```

Stopping the bot never closes a position it already opened. Check
`check_positions.py` afterwards, every time.
