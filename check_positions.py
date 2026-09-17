"""
Verification script — confirms whether the bot has actually placed (and
filled) paper orders on your Tradier sandbox account. Run this any time you
want proof beyond the GitHub Actions logs.

Usage:
    export TRADIER_TOKEN=your_sandbox_token
    export TRADIER_ACCOUNT_ID=your_sandbox_account_id
    python check_positions.py
"""
from tradier_client import TradierClient


def main():
    client = TradierClient()

    print("=" * 70)
    print("ACCOUNT BALANCE")
    print("=" * 70)
    try:
        print(client.get_balances())
    except Exception as e:
        print(f"ERROR fetching balance: {e}")

    print()
    print("=" * 70)
    print("OPEN POSITIONS")
    print("=" * 70)
    positions = client.get_positions()
    if not positions:
        print("No open positions.")
    else:
        for p in positions:
            print(f"  {p.get('symbol'):<20} qty={p.get('quantity')}  "
                  f"cost_basis={p.get('cost_basis')}  date_acquired={p.get('date_acquired')}")

    print()
    print("=" * 70)
    print("RECENT ORDERS (today)")
    print("=" * 70)
    try:
        orders = client.get_orders()
        if not orders:
            print("No orders found.")
        for o in orders:
            print(f"  id={o.get('id')}  class={o.get('class')}  status={o.get('status')}  "
                  f"symbol={o.get('symbol')}  type={o.get('type')}  "
                  f"price={o.get('price')}  avg_fill_price={o.get('avg_fill_price')}  "
                  f"created={o.get('create_date')}")
    except Exception as e:
        print(f"ERROR fetching orders: {e}")

    print()
    print("If you see an order above with status 'filled' and a matching")
    print("entry in state.json, the bot genuinely executed a paper trade.")
    print("status 'pending'/'open' means it's submitted but not yet filled —")
    print("check again in a moment. status 'rejected'/'error' means it failed;")
    print("check reason_description in the raw order object for why.")


if __name__ == "__main__":
    main()
