"""
Entry script — intended to run on a schedule around 9:33-9:47 AM ET.

Safety default: CONFIG["dry_run"] = True. In dry-run mode this previews the
order (Tradier validates it and returns estimated cost/commission) but does
NOT submit it or change state. Flip dry_run to False only after you've
reviewed several days of preview output and are comfortable with it —
even though this is paper money, treat it like a real system: verify
before you automate.

Runs the full filter gate (variance risk premium, VIX trend, short-vol
stagnation, news day) via filters.py before ever building an order — see
that module's docstring for the Tradier-symbol caveats around the VIX
family of indices.

Every run appends exactly one row to decision_log.csv (keyed by date, so a
re-run the same day overwrites that day's row rather than duplicating it),
recording whether the bot entered, previewed, or skipped, and why. Check
that file (or the "Entry decision" section of the GitHub Actions run
summary) instead of digging through logs to answer "did it trade today?".
"""
import sys
import os
import csv
from datetime import datetime, date
import pytz

from tradier_client import TradierClient
from state_store import load_state, save_state, new_day_reset_if_needed
import filters

CONFIG = {
    "underlying": "SPY",
    "wing_width": 10,             # points; widen this if trading SPX/index-scale
    "entry_window_start": "09:35",
    "entry_window_end": "09:45",
    "timezone": "America/New_York",
    "dry_run": True,              # SAFETY DEFAULT — see docstring above

    # Filter thresholds — same defaults as the standalone yfinance checker
    "realized_vol_window": 5,
    "iv_forward_target_days": 7,
    "vrp_min_ratio": 1.0,
    "vix_trend_lookback": 5,
    "vix_trend_max_up_pct": 1.0,
    "vix1d_stagnant_max_std_pct": 8.0,
    "vix1d_stagnant_max_intraday_range_pct": 15.0,
    "news_events_file": "econ_events.csv",
}

DECISION_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "decision_log.csv")


def log_decision(decision, reason):
    """Append/overwrite today's row in decision_log.csv: date, decision, reason.

    Keyed by date so re-running entry.py the same day (e.g. manual
    workflow_dispatch after a real scheduled run) updates that day's row
    instead of creating duplicates — same pattern report.py uses for
    equity_curve.csv.
    """
    today = date.today().isoformat()
    rows = {}
    if os.path.exists(DECISION_LOG_PATH):
        with open(DECISION_LOG_PATH, newline="") as f:
            for row in csv.DictReader(f):
                rows[row["date"]] = row
    rows[today] = {"date": today, "decision": decision, "reason": reason}
    with open(DECISION_LOG_PATH, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "decision", "reason"])
        w.writeheader()
        for day in sorted(rows):
            w.writerow(rows[day])
    print(f"\nDECISION: {decision} — {reason}")


def in_entry_window():
    tz = pytz.timezone(CONFIG["timezone"])
    now = datetime.now(tz)
    sh, sm = map(int, CONFIG["entry_window_start"].split(":"))
    eh, em = map(int, CONFIG["entry_window_end"].split(":"))
    start = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
    end = now.replace(hour=eh, minute=em, second=0, microsecond=0)
    return start <= now <= end, now


def nearest(options, target, opt_type):
    candidates = [o for o in options if o.get("option_type") == opt_type and o.get("strike") is not None]
    if not candidates:
        raise RuntimeError(f"No {opt_type} strikes found in chain")
    return min(candidates, key=lambda o: abs(o["strike"] - target))


def mid_price(o):
    b, a = o.get("bid"), o.get("ask")
    if b is not None and a is not None and b > 0 and a > 0:
        return (b + a) / 2
    return o.get("last") or 0


def main():
    ok, now = in_entry_window()
    if not ok:
        print(f"Outside entry window (current ET time: {now.strftime('%H:%M:%S')}) — exiting without action.")
        log_decision("SKIPPED", "outside entry window (09:35-09:45 ET)")
        return

    # --- Filter gate: all must pass or we don't enter ---
    client = TradierClient()

    filter_failures = []

    try:
        events = filters.check_news_day(CONFIG["news_events_file"])
        if events:
            filter_failures.append(f"News day: flagged event(s) today — {', '.join(events)}")
        else:
            print("PASS: no major news day")
    except Exception as e:
        filter_failures.append(f"News day check ERROR: {e}")

    rv = iv = None
    try:
        rv = filters.get_realized_vol(client, CONFIG["underlying"], CONFIG["realized_vol_window"])
        print(f"  {CONFIG['realized_vol_window']}-day realized vol: {rv:.2f}%")
    except Exception as e:
        filter_failures.append(f"Realized vol ERROR: {e}")

    try:
        iv, iv_exp, iv_days = filters.get_forward_iv(client, CONFIG["underlying"], CONFIG["iv_forward_target_days"])
        print(f"  ~{CONFIG['iv_forward_target_days']}-day forward IV (exp {iv_exp}, {iv_days}d out): {iv:.2f}%")
    except Exception as e:
        filter_failures.append(f"Forward IV ERROR: {e}")

    if rv is not None and iv is not None:
        ratio = iv / rv
        if ratio < CONFIG["vrp_min_ratio"]:
            filter_failures.append(f"VRP: IV/RV ratio {ratio:.2f} < required {CONFIG['vrp_min_ratio']}")
        else:
            print(f"PASS: VRP (IV/RV ratio {ratio:.2f})")
    else:
        filter_failures.append("VRP: could not compute (see IV/RV errors above)")

    try:
        vix_sym, vix_change, vix_last = filters.get_vix_trend(client, CONFIG["vix_trend_lookback"])
        if vix_change > CONFIG["vix_trend_max_up_pct"]:
            filter_failures.append(f"VIX trend: {vix_sym} up {vix_change:+.2f}% over lookback (too much)")
        else:
            print(f"PASS: VIX trend ({vix_sym} {vix_change:+.2f}% over lookback, last {vix_last:.2f})")
    except Exception as e:
        filter_failures.append(f"VIX trend ERROR: {e}")

    try:
        s_sym, std_pct, last_val, method = filters.get_short_vol_stagnation(client, CONFIG["vix_trend_lookback"])
        threshold = (CONFIG["vix1d_stagnant_max_intraday_range_pct"] if "intraday range" in method
                     else CONFIG["vix1d_stagnant_max_std_pct"])
        if std_pct > threshold:
            filter_failures.append(f"Short-vol stagnation: {s_sym} [{method}] {std_pct:.2f}% > {threshold}%")
        else:
            print(f"PASS: short-vol stagnation ({s_sym} [{method}] {std_pct:.2f}%)")
    except Exception as e:
        filter_failures.append(f"Short-vol stagnation ERROR: {e}")

    if filter_failures:
        print("FILTERS FAILED — not entering today:")
        for f in filter_failures:
            print(f"  - {f}")
        log_decision("SKIPPED", "; ".join(filter_failures))
        return

    print("All filters passed — proceeding to strike selection.")

    state = new_day_reset_if_needed(load_state())
    if state["status"] != "flat":
        print(f"State is '{state['status']}' — already acted today. Exiting.")
        log_decision("SKIPPED", f"already acted today (state={state['status']})")
        return

    quote = client.get_quote(CONFIG["underlying"])
    if not quote or "last" not in quote:
        print("Could not get a quote — exiting.")
        log_decision("SKIPPED", "no quote available for underlying")
        return
    spot = quote["last"]

    today_str = date.today().isoformat()
    expirations = client.get_expirations(CONFIG["underlying"])
    exp = today_str if today_str in expirations else (expirations[0] if expirations else None)
    if not exp:
        print("No expirations available — exiting.")
        log_decision("SKIPPED", "no option expirations available")
        return

    chain = client.get_chain(CONFIG["underlying"], exp)
    if not chain:
        print("Empty options chain — exiting.")
        log_decision("SKIPPED", "empty options chain")
        return

    atm_call = nearest(chain, spot, "call")
    atm_put = nearest(chain, spot, "put")
    straddle = mid_price(atm_call) + mid_price(atm_put)
    half_move = straddle / 2

    short_call = nearest(chain, spot + half_move, "call")
    short_put = nearest(chain, spot - half_move, "put")
    long_call = nearest(chain, short_call["strike"] + CONFIG["wing_width"], "call")
    long_put = nearest(chain, short_put["strike"] - CONFIG["wing_width"], "put")

    net_credit = (mid_price(short_call) - mid_price(long_call)) + (mid_price(short_put) - mid_price(long_put))
    net_credit = round(net_credit, 2)

    legs = [
        {"option_symbol": short_call["symbol"], "side": "sell_to_open"},
        {"option_symbol": long_call["symbol"], "side": "buy_to_open"},
        {"option_symbol": short_put["symbol"], "side": "sell_to_open"},
        {"option_symbol": long_put["symbol"], "side": "buy_to_open"},
    ]

    print(f"Spot: {spot} | Straddle: {straddle:.2f} | Target net credit: {net_credit}")
    print(f"  SELL {short_call['symbol']} (strike {short_call['strike']})")
    print(f"  BUY  {long_call['symbol']} (strike {long_call['strike']})")
    print(f"  SELL {short_put['symbol']} (strike {short_put['strike']})")
    print(f"  BUY  {long_put['symbol']} (strike {long_put['strike']})")

    if net_credit <= 0:
        print("Net credit is zero or negative — bad data or no real edge. Exiting without placing an order.")
        log_decision("SKIPPED", f"net credit non-positive ({net_credit})")
        return

    if CONFIG["dry_run"]:
        result = client.preview_multileg_order(
            CONFIG["underlying"], legs, order_type="credit", price=net_credit, tag="0dte-ic"
        )
        print("PREVIEW result (no order placed):", result)
        print("dry_run=True — set CONFIG['dry_run']=False to actually submit paper orders.")
        log_decision(
            "PREVIEWED (dry-run)",
            f"would enter — credit={net_credit}, short_call={short_call['symbol']}, short_put={short_put['symbol']}",
        )
        return

    result = client.place_multileg_order(
        CONFIG["underlying"], legs, order_type="credit", price=net_credit, tag="0dte-ic"
    )
    print("Order submitted:", result)

    order_id = (result.get("order") or {}).get("id")
    state.update({
        "status": "open",
        "position": {
            "expiration": exp,
            "entry_credit": net_credit,
            "short_call": short_call["symbol"], "long_call": long_call["symbol"],
            "short_put": short_put["symbol"], "long_put": long_put["symbol"],
            "short_call_strike": short_call["strike"], "short_put_strike": short_put["strike"],
            "order_id": order_id,
            "entered_at": now.isoformat(),
            "call_side_closed": False,
            "put_side_closed": False,
        },
    })
    save_state(state)
    log_decision(
        "ENTERED",
        f"credit={net_credit}, short_call={short_call['symbol']}, short_put={short_put['symbol']}, order_id={order_id}",
    )


if __name__ == "__main__":
    main()
