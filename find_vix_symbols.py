"""
One-time diagnostic: find the correct Tradier ticker symbols for VIX,
VIX1D, and VIX9D. Run this once you have sandbox credentials, then update
the *_SYMBOL_CANDIDATES lists in filters.py with whatever actually works.

Usage:
    export TRADIER_TOKEN=your_sandbox_token
    export TRADIER_ACCOUNT_ID=your_sandbox_account_id
    python find_vix_symbols.py
"""
from tradier_client import TradierClient


def search_and_lookup(client, keyword):
    print(f"\n--- Searching for '{keyword}' ---")
    try:
        data = client.search(keyword)
        securities = (data.get("securities") or {}).get("security")
        securities = securities if isinstance(securities, list) else ([securities] if securities else [])
        if securities:
            print("Market Search results:")
            for s in securities[:10]:
                print(f"  {s.get('symbol'):<12} {s.get('description', '')}")
        else:
            print("Market Search: no results")
    except Exception as e:
        print(f"Market Search ERROR: {e}")

    try:
        data = client.lookup(keyword)
        securities = (data.get("securities") or {}).get("security")
        securities = securities if isinstance(securities, list) else ([securities] if securities else [])
        if securities:
            print("Lookup Symbols results:")
            for s in securities[:10]:
                print(f"  {s.get('symbol'):<12} {s.get('description', '')}")
        else:
            print("Lookup Symbols: no results")
    except Exception as e:
        print(f"Lookup Symbols ERROR: {e}")


def try_direct_quotes(client, candidates):
    print(f"\n--- Trying direct quotes for candidates: {candidates} ---")
    for sym in candidates:
        try:
            q = client.get_quote(sym)
            if q and q.get("last") is not None:
                print(f"  ✅ {sym:<10} WORKS — last={q.get('last')}, "
                      f"high={q.get('high')}, low={q.get('low')}")
            else:
                print(f"  ❌ {sym:<10} returned no usable quote")
        except Exception as e:
            print(f"  ❌ {sym:<10} ERROR: {e}")


def main():
    client = TradierClient()

    for keyword in ["VIX", "CBOE Volatility", "VIX1D", "VIX9D"]:
        search_and_lookup(client, keyword)

    all_candidates = [
        "VIX", "$VIX", "VIX.X", "^VIX",
        "VIX1D", "$VIX1D", "VIX1D.X",
        "VIX9D", "$VIX9D", "VIX9D.X",
    ]
    try_direct_quotes(client, all_candidates)

    print("\nDone. Whatever printed a ✅ above is what you should put in")
    print("filters.py's VIX_SYMBOL_CANDIDATES / VIX1D_SYMBOL_CANDIDATES /")
    print("VIX9D_SYMBOL_CANDIDATES lists (put the working one first).")


if __name__ == "__main__":
    main()
