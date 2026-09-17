"""
Performance report for the 0DTE iron condor bot.

Pulls everything from Tradier directly rather than from the bot's own logs,
so the numbers are the account's numbers, not the bot's opinion of them.

    python report.py                 # last 30 days
    python report.py --days 90       # longer window
    python report.py --snapshot      # also append/update today's row in equity_curve.csv
    python report.py --orders        # also dump raw recent orders

Requires TRADIER_TOKEN and TRADIER_ACCOUNT_ID in the environment, same as
every other script here.

Caveats worth remembering when you read the output:
  * Sandbox market data is delayed 15 minutes, so fills (and therefore P&L)
    are struck against delayed prices. Treat the numbers as directionally
    useful, not as a live-trading forecast.
  * Options that expire rather than being closed may not show up in the
    gain/loss feed the way a closed round trip does. Cross-check the
    positions list if a day's P&L looks like it went missing.
"""
import argparse
import csv
import os
from collections import defaultdict
from datetime import date, datetime, timedelta

from tradier_client import TradierClient

EQUITY_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "equity_curve.csv")

OPEN_TAGS = ("0dte-ic",)
CLOSE_TAGS = ("0dte-ic-close", "0dte-ic-defend-call", "0dte-ic-defend-put")


# ---------------------------------------------------------------------------
# Raw fetches (endpoints the minimal client doesn't wrap yet)
# ---------------------------------------------------------------------------

def fetch_gainloss(client, start, end):
    """Closed positions with realized P&L, per leg."""
    try:
        data = client._get(
            f"/accounts/{client.account_id}/gainloss",
            {"page": 1, "limit": 500, "sortBy": "closeDate", "sort": "desc",
             "start": start, "end": end},
        )
    except Exception as e:
        print(f"  (gain/loss endpoint unavailable: {e})")
        return []
    gl = data.get("gainloss")
    if not gl or gl == "null":
        return []
    rows = gl.get("closed_position")
    if rows is None:
        return []
    return rows if isinstance(rows, list) else [rows]


def fetch_history(client, start, end):
    """Account activity feed — trades, expirations, adjustments."""
    try:
        data = client._get(
            f"/accounts/{client.account_id}/history",
            {"page": 1, "limit": 500, "start": start, "end": end},
        )
    except Exception as e:
        print(f"  (history endpoint unavailable: {e})")
        return []
    h = data.get("history")
    if not h or h == "null":
        return []
    events = h.get("event")
    if events is None:
        return []
    return events if isinstance(events, list) else [events]


# ---------------------------------------------------------------------------
# Trade grouping
# ---------------------------------------------------------------------------

def group_closed_trades(closed_positions):
    """One 0DTE condor = up to four legs closed the same day. Group by close
    date and sum, which is the right unit of analysis for this strategy."""
    by_day = defaultdict(lambda: {"pl": 0.0, "legs": 0, "symbols": []})
    for p in closed_positions:
        day = (p.get("close_date") or "")[:10]
        if not day:
            continue
        try:
            pl = float(p.get("gain_loss") or 0.0)
        except (TypeError, ValueError):
            pl = 0.0
        by_day[day]["pl"] += pl
        by_day[day]["legs"] += 1
        by_day[day]["symbols"].append(p.get("symbol"))
    return dict(sorted(by_day.items()))


def pl_from_orders(orders):
    """Fallback / cross-check: reconstruct today's net cash from filled legs.
    Sells are credits, buys are debits, options are x100."""
    net = 0.0
    counted = 0
    for o in orders:
        if o.get("status") != "filled":
            continue
        tag = o.get("tag") or ""
        if tag not in OPEN_TAGS + CLOSE_TAGS:
            continue
        legs = o.get("leg")
        legs = legs if isinstance(legs, list) else ([legs] if legs else [o])
        for leg in legs:
            try:
                price = float(leg.get("avg_fill_price") or 0.0)
                qty = float(leg.get("exec_quantity") or leg.get("quantity") or 0.0)
            except (TypeError, ValueError):
                continue
            side = (leg.get("side") or "").lower()
            if price <= 0 or qty <= 0:
                continue
            sign = 1.0 if side.startswith("sell") else -1.0
            net += sign * price * qty * 100
            counted += 1
    return net, counted


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_balances(client):
    print("=" * 72)
    print("ACCOUNT")
    print("=" * 72)
    try:
        b = client.get_balances()
    except Exception as e:
        print(f"  ERROR fetching balances: {e}")
        return None
    fields = [
        ("total_equity", "Total equity"),
        ("total_cash", "Cash"),
        ("market_value", "Market value of positions"),
        ("open_pl", "Open P&L"),
        ("close_pl", "Closed P&L (today)"),
        ("option_buying_power", "Option buying power"),
    ]
    for key, label in fields:
        if b.get(key) is not None:
            print(f"  {label:<28} {b[key]}")
    return b


def print_open_positions(client):
    print()
    print("=" * 72)
    print("OPEN POSITIONS")
    print("=" * 72)
    try:
        positions = client.get_positions()
    except Exception as e:
        print(f"  ERROR fetching positions: {e}")
        return
    if not positions:
        print("  Flat — no open positions.")
        return
    for p in positions:
        print(f"  {str(p.get('symbol')):<22} qty={p.get('quantity'):<6} "
              f"cost_basis={p.get('cost_basis')}  acquired={p.get('date_acquired')}")


def print_trade_table(trades):
    print()
    print("=" * 72)
    print("CLOSED TRADES (grouped by close date)")
    print("=" * 72)
    if not trades:
        print("  Nothing closed in this window yet.")
        return
    print(f"  {'DATE':<12} {'LEGS':>5} {'P&L':>12}   RUNNING")
    running = 0.0
    for day, t in trades.items():
        running += t["pl"]
        print(f"  {day:<12} {t['legs']:>5} {t['pl']:>12,.2f}   {running:>10,.2f}")


def print_summary(trades):
    print()
    print("=" * 72)
    print("SUMMARY")
    print("=" * 72)
    pls = [t["pl"] for t in trades.values()]
    if not pls:
        print("  No closed trades to summarise.")
        return
    wins = [p for p in pls if p > 0]
    losses = [p for p in pls if p < 0]
    total = sum(pls)
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))

    def line(label, value):
        print(f"  {label:<28} {value}")

    line("Trades", len(pls))
    line("Wins / losses", f"{len(wins)} / {len(losses)}")
    line("Win rate", f"{(len(wins) / len(pls) * 100):.1f}%")
    line("Net P&L", f"{total:,.2f}")
    line("Average trade", f"{(total / len(pls)):,.2f}")
    if wins:
        line("Average win", f"{(gross_win / len(wins)):,.2f}")
        line("Largest win", f"{max(wins):,.2f}")
    if losses:
        line("Average loss", f"{(sum(losses) / len(losses)):,.2f}")
        line("Largest loss", f"{min(losses):,.2f}")
    if gross_loss > 0:
        line("Profit factor", f"{(gross_win / gross_loss):.2f}")
    else:
        line("Profit factor", "n/a (no losing trades yet)")

    # Worst peak-to-trough run on the closed-trade equity curve.
    peak = 0.0
    equity = 0.0
    max_dd = 0.0
    for p in pls:
        equity += p
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    line("Max drawdown (closed)", f"{max_dd:,.2f}")

    print()
    print("  A win rate on its own says very little for a premium-selling")
    print("  strategy — most of these will win. Watch average loss versus")
    print("  average win and the drawdown line instead.")


def snapshot_equity(balances, open_positions_count):
    if not balances:
        print("\n  Skipping equity snapshot — no balances available.")
        return
    today = date.today().isoformat()
    rows = {}
    header = ["date", "total_equity", "total_cash", "market_value", "open_positions"]
    if os.path.exists(EQUITY_CSV):
        with open(EQUITY_CSV, newline="") as f:
            for row in csv.DictReader(f):
                rows[row["date"]] = row
    rows[today] = {
        "date": today,
        "total_equity": balances.get("total_equity", ""),
        "total_cash": balances.get("total_cash", ""),
        "market_value": balances.get("market_value", ""),
        "open_positions": open_positions_count,
    }
    with open(EQUITY_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        for day in sorted(rows):
            w.writerow(rows[day])
    print(f"\n  Equity snapshot written for {today} -> {os.path.basename(EQUITY_CSV)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30, help="lookback window in days")
    ap.add_argument("--snapshot", action="store_true", help="append today's equity to equity_curve.csv")
    ap.add_argument("--orders", action="store_true", help="dump raw recent orders")
    args = ap.parse_args()

    start = (date.today() - timedelta(days=args.days)).isoformat()
    end = date.today().isoformat()

    client = TradierClient()
    print(f"Report window: {start} -> {end}  (generated {datetime.now():%Y-%m-%d %H:%M:%S})")
    print()

    balances = print_balances(client)

    try:
        positions = client.get_positions()
    except Exception:
        positions = []
    print_open_positions(client)

    closed = fetch_gainloss(client, start, end)
    trades = group_closed_trades(closed)
    print_trade_table(trades)
    print_summary(trades)

    # Cross-check today's activity against the order feed.
    try:
        orders = client.get_orders()
    except Exception as e:
        orders = []
        print(f"\n  (orders endpoint unavailable: {e})")
    if orders:
        net, legs = pl_from_orders(orders)
        if legs:
            print()
            print(f"  Cross-check from today's filled bot legs ({legs} legs): "
                  f"net cash {net:,.2f}")
            print("  (positive = net credit taken in and not yet paid back)")

    if args.orders:
        print()
        print("=" * 72)
        print("RAW RECENT ORDERS")
        print("=" * 72)
        for o in orders:
            print(f"  id={o.get('id')} class={o.get('class')} status={o.get('status')} "
                  f"tag={o.get('tag')} type={o.get('type')} price={o.get('price')} "
                  f"avg_fill={o.get('avg_fill_price')} created={o.get('create_date')}")
            if o.get("status") in ("rejected", "error"):
                print(f"      reason: {o.get('reason_description')}")

    if args.snapshot:
        snapshot_equity(balances, len(positions))


if __name__ == "__main__":
    main()
