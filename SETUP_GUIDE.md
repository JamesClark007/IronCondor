# Complete Setup Guide: Running the 0DTE Bot End-to-End

This walks through every step from "nothing exists yet" to "confirmed real
paper trades are executing automatically." Follow it in order — each stage
has a checkpoint so you know it's actually working before moving on.

---

## Stage 1 — Get Tradier sandbox credentials

1. Go to https://developer.tradier.com and create an account.
2. Create a new application. Choose **Sandbox** (not production).
3. Once created, you'll be given two things — save both:
   - **Access Token** (this is `TRADIER_TOKEN`)
   - **Sandbox Account Number** (looks like `VA00000000` — this is
     `TRADIER_ACCOUNT_ID`)
4. Sandbox accounts come pre-funded with paper money automatically — no
   further action needed there.

**Checkpoint:** you should have two strings saved somewhere safe (a token
and an account number). Don't commit them to any file or repo — they go in
GitHub Secrets in Stage 3.

---

## Stage 2 — Test locally before touching GitHub

Do this on your own computer first. It's much faster to debug here than
through Actions logs.

```bash
# In the tradier-0dte-bot folder:
pip install -r requirements.txt

export TRADIER_TOKEN=your_token_here
export TRADIER_ACCOUNT_ID=your_account_id_here
```

(Windows PowerShell: use `$env:TRADIER_TOKEN="..."` instead of `export`.)

**2a. Find the real VIX symbols:**
```bash
python find_vix_symbols.py
```
Look for ✅ marks in the output. Open `filters.py` and put whichever symbols
actually worked at the **front** of `VIX_SYMBOL_CANDIDATES`,
`VIX1D_SYMBOL_CANDIDATES`, and `VIX9D_SYMBOL_CANDIDATES`.

**2b. Confirm your account is reachable:**
```bash
python check_positions.py
```
You should see a balance printout and "No open positions" / "No orders
found" (expected — you haven't traded yet). If this errors, your token or
account ID is wrong — fix that before continuing.

**2c. Test the entry logic (only works 9:35-9:45 AM ET on a trading day):**
```bash
python entry.py
```
With `dry_run = True` (the default), this will print the filter results and,
if they pass, a **preview** of the order — no real submission happens.
Outside that time window, it'll just print "Outside entry window" and exit,
which is correct behavior, not a bug.

If you want to test the mechanics right now regardless of time, temporarily
widen the window in `entry.py`:
```python
"entry_window_start": "00:00",
"entry_window_end": "23:59",
```
**Revert this before you rely on it for real** — it exists only for testing.

**Checkpoint:** `entry.py` runs, filters print PASS/FAIL with real numbers,
and (if filters passed) you see a PREVIEW result with real strikes and a
credit estimate.

---

## Stage 3 — Push to GitHub and wire up Actions

1. Create a new GitHub repository (private is fine — Actions minutes are
   still free on the tiers relevant here).
2. Push the entire `tradier-0dte-bot` folder to it, including the hidden
   `.github/workflows/` directory:
   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
   git push -u origin main
   ```
3. In the repo: **Settings → Secrets and variables → Actions → New
   repository secret**. Add two secrets:
   - `TRADIER_TOKEN`
   - `TRADIER_ACCOUNT_ID`
4. **Settings → Actions → General** — confirm "Allow all actions and
   reusable workflows" is selected, and Workflow permissions allows
   read/write (needed so the workflow can commit `state.json` back).

**Checkpoint:** go to the **Actions** tab — you should see `0dte-entry` and
`0dte-manage` listed as workflows (they won't have run yet).

---

## Stage 4 — Manually trigger a test run in CI

Don't wait for the schedule the first time — trigger it by hand so you can
watch the logs live.

1. Actions tab → click **0dte-entry** → **Run workflow** button (this uses
   the `workflow_dispatch` trigger already in `entry.yml`) → **Run workflow**.
2. Click into the run once it starts, watch the logs.
3. If it's outside 9:35-9:45 ET, you'll correctly see "Outside entry window"
   — that's the script working as intended, not an error. To actually see it
   act, either wait for a real weekday morning, or temporarily widen the
   window as in Stage 2c, commit that change, run it, then **revert and
   commit again**.

**Checkpoint:** the Actions log shows the same filter PASS/FAIL and
PREVIEW output you saw locally in Stage 2c.

---

## Stage 5 — Let it run for real, still in dry-run

Leave `dry_run = True` and let the scheduled workflows run untouched for a
few real trading days:

- `0dte-entry` fires automatically ~9:33-9:47 AM ET on weekdays
- `0dte-manage` fires every ~5 minutes during market hours

Check the Actions tab each day. You're looking for: consistent filter
behavior, sensible strikes, and preview credit numbers that look reasonable
against what you'd expect that morning. This is the same "watch it think"
period we discussed before touching real submission.

**Checkpoint:** several days of clean preview logs you're comfortable with.

---

## Stage 6 — Go live on paper (the actual "execute trades" step)

Once you trust the preview output:

1. Edit `entry.py`: set `"dry_run": False`
2. Edit `manage.py`: set `"dry_run": False`
3. Commit and push both changes.
4. Wait for (or manually trigger, if within the time window) the next
   `0dte-entry` run.

**This is the step that actually places paper orders on Tradier's sandbox.**
Everything before this point only previewed.

---

## Stage 7 — Prove to yourself it actually executed

Don't just trust the Actions log — verify against the account directly:

```bash
python check_positions.py
```

Look for:
- **OPEN POSITIONS** section listing 4 option legs (short call, long call,
  short put, long put) with real OCC symbols
- **RECENT ORDERS** section showing an order with `class=multileg`,
  `status=filled` (or `open`/`pending` if not yet filled), and a real
  `avg_fill_price`

You can also cross-check against `state.json` in the repo — after a real
(non-dry-run) entry, it should show `"status": "open"` with the same strikes
and credit you see in `check_positions.py`'s output. If both agree, the bot
genuinely executed a paper trade — this isn't just a log message, it's a
real order sitting in your Tradier sandbox account.

**Checkpoint:** `check_positions.py` shows a real, filled (or working)
4-leg order matching what `state.json` recorded.

---

## Stage 8 — Watch it manage the position

Over the next `0dte-manage` runs (every ~5 min), watch the Actions logs for:
- Either the 25% profit target firing and closing everything, or
- A tested-strike defense closing one side, or
- "No action needed" repeatedly, until one of the above eventually happens

After a close, re-run `check_positions.py` — positions should be flat again,
and `state.json` should show `"status": "closed"` (full close) or one side
marked `closed` (partial defense).

---

## Ongoing maintenance

- Keep `econ_events.csv` updated weekly with FOMC/CPI/NFP/PCE dates.
- Periodically re-run `check_positions.py` to sanity-check against
  `state.json` — they should never disagree.
- If a workflow goes quiet, check Settings → Actions → General for the
  60-day-inactivity auto-disable, and re-enable if needed.
