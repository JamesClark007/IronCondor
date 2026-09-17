"""
Management script — intended to run every ~5 minutes during market hours.

Checks an open position (from state.json) for:
  1. 25% profit target -> close whatever is still open
  2. A tested short strike whose cost-to-close has reached the total net
     credit -> close just that side, leave the other side to expire worthless
  3. End of session -> flatten anything still open rather than carrying
     0DTE contracts into expiry

Same dry_run safety default as entry.py — flip to False only once you trust
the preview output.

LIMITATION: this only checks whatever moment the workflow happens to run
(every ~5 min at best, and GitHub Actions scheduled runs can be delayed
further, especially during high load). It cannot react intra-minute the way
watching a live platform can. A sharp move between checks could move the
position past where the 25%/defense rule would have caught it earlier.

Changes from the original version (see RUNBOOK.md, "Fixes applied"):
  * Only the legs actually still open are priced and closed. The original
    priced all four legs even after a defensive close, which both corrupted
    the profit-target math and would have submitted close orders for legs
    the account no longer held.
  * What was already paid on a defensive close is carried in
    position["paid_to_close"], so the 25% target stays honest afterwards.
  * Added a session-end flatten so a 0DTE condor is not left to expire.
  * Warns loudly instead of silently forgetting a position left open from a
    previous day.
"""
from datetime import datetime
import pytz

from tradier_client import TradierClient
from state_store import load_state, save_state, new_day_reset_if_needed

CONFIG = {
    "underlying": "SPY",
    "profit_target_pct": 0.25,
    "timezone": "America/New_York",
    "session_start": "09:30",
    "flatten_after": "15:45",   # flatten anything still open after this ET time
    "session_end": "16:00",
    "dry_run": True,   # SAFETY DEFAULT — see docstring above
}


def leg_mid(client, symbol):
    q = client.get_quote(symbol)
    if not q:
        return None
    b, a = q.get("bid"), q.get("ask")
    if b is not None and a is not None and b > 0 and a > 0:
        return (b + a) / 2
    return q.get("last")


def _now_et():
    return datetime.now(pytz.timezone(CONFIG["timezone"]))


def _after(now, hhmm_str):
    h, m = map(int, hhmm_str.split(":"))
    return (now.hour, now.minute) >= (h, m)


def main():
    raw = load_state()
    state = new_day_reset_if_needed(raw)
    if raw.get("status") == "open" and state.get("status") == "flat":
        print("WARNING: state.json carried an OPEN position dated "
              f"{raw.get('date')}, which is not today. It has been reset to flat "
              "and will NOT be managed. Check the account manually — those legs "
              "either expired or are still sitting there.")

    if state["status"] != "open":
        print(f"State is '{state['status']}' — nothing to manage.")
        return

    now = _now_et()
    if not _after(now, CONFIG["session_start"]):
        print(f"Before session start (ET {now:%H:%M:%S}) — skipping.")
        return
    if _after(now, CONFIG["session_end"]):
        print(f"After session end (ET {now:%H:%M:%S}) — skipping.")
        return

    pos = state["position"]
    client = TradierClient()

    call_open = not pos.get("call_side_closed")
    put_open = not pos.get("put_side_closed")
    if not call_open and not put_open:
        print("Both sides already closed — marking position flat.")
        state.update({"status": "closed", "position": None})
        save_state(state)
        return

    sc = lc = sp = lp = None
    if call_open:
        sc = leg_mid(client, pos["short_call"])
        lc = leg_mid(client, pos["long_call"])
    if put_open:
        sp = leg_mid(client, pos["short_put"])
        lp = leg_mid(client, pos["long_put"])

    if (call_open and (sc is None or lc is None)) or (put_open and (sp is None or lp is None)):
        print("Missing a leg price this check — skipping (will retry next scheduled run).")
        return

    call_cost = (sc - lc) if call_open else 0.0
    put_cost = (sp - lp) if put_open else 0.0
    cost_to_close = call_cost + put_cost

    entry_credit = pos["entry_credit"]
    already_paid = pos.get("paid_to_close", 0.0)
    captured = entry_credit - already_paid - cost_to_close
    pct_captured = captured / entry_credit if entry_credit else 0

    sides = " + ".join(s for s, o in (("call", call_open), ("put", put_open)) if o)
    print(f"Open side(s): {sides} | Entry credit: {entry_credit} | "
          f"Already paid: {already_paid:.2f} | Cost to close remaining: {cost_to_close:.2f} | "
          f"Captured: {pct_captured * 100:.1f}%")

    def remaining_legs():
        legs = []
        if call_open:
            legs += [
                {"option_symbol": pos["short_call"], "side": "buy_to_close"},
                {"option_symbol": pos["long_call"], "side": "sell_to_close"},
            ]
        if put_open:
            legs += [
                {"option_symbol": pos["short_put"], "side": "buy_to_close"},
                {"option_symbol": pos["long_put"], "side": "sell_to_close"},
            ]
        return legs

    # --- 1) 25% profit target: close whatever is still open ---
    if pct_captured >= CONFIG["profit_target_pct"]:
        print("PROFIT TARGET HIT — closing remaining legs.")
        _close(client, remaining_legs(), round(cost_to_close, 2),
               "0dte-ic-close", state, full_close=True)
        return

    # --- 2) End-of-session flatten: do not carry 0DTE into expiry ---
    if _after(now, CONFIG["flatten_after"]):
        print(f"Past {CONFIG['flatten_after']} ET — flattening remaining legs.")
        _close(client, remaining_legs(), round(cost_to_close, 2),
               "0dte-ic-eod", state, full_close=True)
        return

    # --- 3) Tested-strike defense: close only the side at/above total credit ---
    quote = client.get_quote(CONFIG["underlying"])
    spot = quote["last"] if quote else None
    if spot is None:
        print("Could not get underlying quote for defense check — skipping.")
        return

    if call_open and spot >= pos["short_call_strike"] and sc >= entry_credit:
        print("Short CALL tested and at total-credit cost — closing call side only.")
        legs = [
            {"option_symbol": pos["short_call"], "side": "buy_to_close"},
            {"option_symbol": pos["long_call"], "side": "sell_to_close"},
        ]
        _close(client, legs, round(call_cost, 2), "0dte-ic-defend-call", state, side="call")
        return

    if put_open and spot <= pos["short_put_strike"] and sp >= entry_credit:
        print("Short PUT tested and at total-credit cost — closing put side only.")
        legs = [
            {"option_symbol": pos["short_put"], "side": "buy_to_close"},
            {"option_symbol": pos["long_put"], "side": "sell_to_close"},
        ]
        _close(client, legs, round(put_cost, 2), "0dte-ic-defend-put", state, side="put")
        return

    print("No action needed this check.")


def _close(client, legs, price, tag, state, full_close=False, side=None):
    if not legs:
        print("Nothing left to close.")
        return

    if CONFIG["dry_run"]:
        result = client.preview_multileg_order(
            CONFIG["underlying"], legs, order_type="debit", price=price, tag=tag)
        print("PREVIEW close result (no order placed):", result)
        return

    result = client.place_multileg_order(
        CONFIG["underlying"], legs, order_type="debit", price=price, tag=tag)
    print("Close order submitted:", result)

    if full_close:
        state.update({"status": "closed", "position": None})
    elif side in ("call", "put"):
        state["position"][f"{side}_side_closed"] = True
        state["position"]["paid_to_close"] = state["position"].get("paid_to_close", 0.0) + price
    save_state(state)


if __name__ == "__main__":
    main()
