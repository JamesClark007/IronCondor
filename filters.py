"""
Entry filters, ported from the standalone yfinance-based checker to run on
live Tradier data instead. Two real upgrades over the yfinance version:

  1. Forward IV comes from the ACTUAL options chain's greeks (mid_iv), not an
     ATM-straddle approximation.
  2. Realized vol is computed on the underlying itself (e.g. SPY) rather than
     a separate index proxy, via Tradier's /markets/history endpoint.

CAVEAT: I could not fully confirm Tradier's exact symbol format for
VIX/VIX1D/VIX9D from public docs. VIX_SYMBOL_CANDIDATES below tries a couple
of common variants and uses whichever one returns real data — verify against
your own sandbox account and adjust the candidate lists if needed.
"""
import csv
import os
from datetime import date, datetime, timedelta

import numpy as np

VIX_SYMBOL_CANDIDATES = ["VIX", "$VIX", "VIX.X"]
VIX1D_SYMBOL_CANDIDATES = ["VIX1D", "$VIX1D"]
VIX9D_SYMBOL_CANDIDATES = ["VIX9D", "$VIX9D"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _annualize(daily_std, trading_days=252):
    return daily_std * np.sqrt(trading_days)


def _history_closes(client, symbol, lookback_days):
    """Return a list of (date_str, close) for the last ~lookback_days calendar
    days of daily history, or None if the symbol/endpoint returned nothing."""
    start = (date.today() - timedelta(days=lookback_days * 3)).isoformat()  # buffer for weekends/holidays
    end = date.today().isoformat()
    try:
        data = client.get_history(symbol, interval="daily", start=start, end=end)
        day = data.get("history", {})
        if not day:
            return None
        rows = day.get("day")
        if rows is None:
            return None
        rows = rows if isinstance(rows, list) else [rows]
        closes = [(r["date"], float(r["close"])) for r in rows if r.get("close") is not None]
        return closes if closes else None
    except Exception:
        return None


def _first_working_symbol(client, candidates, lookback_days, min_rows):
    """Try each candidate symbol, return (symbol, closes) for the first that
    returns at least min_rows of history."""
    errors = []
    for sym in candidates:
        closes = _history_closes(client, sym, lookback_days)
        if closes and len(closes) >= min_rows:
            return sym, closes, errors
        errors.append(f"{sym}: {'no data' if not closes else f'only {len(closes)} rows'}")
    return None, None, errors


# ---------------------------------------------------------------------------
# 1. Realized volatility (on the traded underlying itself)
# ---------------------------------------------------------------------------

def get_realized_vol(client, symbol, window):
    closes = _history_closes(client, symbol, window * 3)
    if not closes or len(closes) < window + 1:
        raise RuntimeError(f"Not enough history for {symbol} (got "
                            f"{len(closes) if closes else 0} rows, need {window + 1})")
    vals = np.array([c for _, c in closes[-(window + 1):]])
    log_returns = np.diff(np.log(vals))
    return _annualize(log_returns.std()) * 100  # percentage


# ---------------------------------------------------------------------------
# 2. Forward IV — pulled directly from the live chain's greeks
# ---------------------------------------------------------------------------

def get_forward_iv(client, symbol, target_days):
    expirations = client.get_expirations(symbol)
    if not expirations:
        raise RuntimeError(f"No expirations available for {symbol}")

    today = datetime.now()
    dated = [(e, (datetime.strptime(e, "%Y-%m-%d") - today).days) for e in expirations]
    future = [e for e in dated if e[1] >= 1]  # exclude 0DTE — we want the ~N-day-out chain
    if not future:
        raise RuntimeError("No future (non-0DTE) expirations found")
    best_exp, actual_days = min(future, key=lambda e: abs(e[1] - target_days))

    chain = client.get_chain(symbol, best_exp)
    if not chain:
        raise RuntimeError(f"Empty chain for {symbol} {best_exp}")

    quote = client.get_quote(symbol)
    spot = quote["last"] if quote else None
    if spot is None:
        raise RuntimeError(f"No spot quote for {symbol}")

    calls = [o for o in chain if o.get("option_type") == "call"]
    puts = [o for o in chain if o.get("option_type") == "put"]
    if not calls or not puts:
        raise RuntimeError("Chain missing calls or puts")

    atm_call = min(calls, key=lambda o: abs(o["strike"] - spot))
    atm_put = min(puts, key=lambda o: abs(o["strike"] - spot))

    ivs = []
    for o in (atm_call, atm_put):
        g = o.get("greeks") or {}
        iv = g.get("mid_iv") or g.get("smv_vol")
        if iv and iv > 0:
            ivs.append(iv)

    if not ivs:
        raise RuntimeError("No valid IV in ATM chain greeks — sandbox data may not include greeks")

    avg_iv = float(np.mean(ivs)) * 100
    return avg_iv, best_exp, actual_days


# ---------------------------------------------------------------------------
# 3. VIX trend
# ---------------------------------------------------------------------------

def get_vix_trend(client, lookback):
    sym, closes, errors = _first_working_symbol(client, VIX_SYMBOL_CANDIDATES, lookback, lookback + 1)
    if sym is None:
        raise RuntimeError("No working VIX symbol found. Tried: " + " | ".join(errors))
    vals = [c for _, c in closes[-(lookback + 1):]]
    change_pct = (vals[-1] - vals[0]) / vals[0] * 100
    return sym, change_pct, vals[-1]


# ---------------------------------------------------------------------------
# 4. VIX1D / VIX9D stagnation, with intraday-range and VIX-stdev fallbacks
# ---------------------------------------------------------------------------

def get_short_vol_stagnation(client, lookback):
    errors = []

    for candidates in (VIX1D_SYMBOL_CANDIDATES, VIX9D_SYMBOL_CANDIDATES):
        sym, closes, errs = _first_working_symbol(client, candidates, lookback, lookback + 1)
        errors.extend(errs)
        if sym is not None:
            vals = [c for _, c in closes[-lookback:]]
            std_pct = float(np.std(vals) / np.mean(vals) * 100)
            return sym, std_pct, vals[-1], "multi-day stdev"

    # Only 1 (or a few) days of data likely available for these indices —
    # fall back to today's intraday range as a same-day stagnation proxy.
    for candidates in (VIX1D_SYMBOL_CANDIDATES, VIX9D_SYMBOL_CANDIDATES):
        for sym in candidates:
            try:
                q = client.get_quote(sym)
                if q and q.get("high") and q.get("low") and q.get("close"):
                    rng_pct = (q["high"] - q["low"]) / q["close"] * 100
                    errors.append(f"{sym}: using intraday range fallback")
                    return sym, rng_pct, q["close"], "intraday range (1-day fallback)"
            except Exception as e:
                errors.append(f"{sym}: {e}")

    # Last resort: VIX's own multi-day stdev.
    sym, closes, errs = _first_working_symbol(client, VIX_SYMBOL_CANDIDATES, lookback, lookback + 1)
    errors.extend(errs)
    if sym is not None:
        vals = [c for _, c in closes[-lookback:]]
        std_pct = float(np.std(vals) / np.mean(vals) * 100)
        return f"{sym} (fallback)", std_pct, vals[-1], "multi-day stdev (VIX fallback)"

    raise RuntimeError("No short-term vol data available. Details: " + " | ".join(errors))


# ---------------------------------------------------------------------------
# 5. News day check (same CSV-based approach as the standalone script)
# ---------------------------------------------------------------------------

def ensure_news_file(path):
    if not os.path.exists(path):
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["date", "event"])
            writer.writerow(["# add rows like: 2026-09-05,NFP"])


def check_news_day(path, target_date=None):
    ensure_news_file(path)
    date_str = target_date.strftime("%Y-%m-%d") if target_date else date.today().strftime("%Y-%m-%d")
    matches = []
    with open(path) as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if not row or row[0].startswith("#"):
                continue
            d, event = row[0].strip(), row[1].strip() if len(row) > 1 else ""
            if d == date_str:
                matches.append(event)
    return matches
