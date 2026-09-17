"""
Minimal Tradier API client for sandbox (paper) trading.
Docs: https://documentation.tradier.com/

Requires two environment variables (set as GitHub Actions secrets, or locally):
  TRADIER_TOKEN        — your sandbox API access token
  TRADIER_ACCOUNT_ID   — your sandbox paper account number (e.g. VA00000000)

Get both by signing up at https://developer.tradier.com and creating a
sandbox/paper trading application.
"""
import os
import requests


class TradierClient:
    def __init__(self, token=None, account_id=None, sandbox=True):
        self.token = token or os.environ.get("TRADIER_TOKEN")
        self.account_id = account_id or os.environ.get("TRADIER_ACCOUNT_ID")
        if not self.token or not self.account_id:
            raise RuntimeError(
                "Missing TRADIER_TOKEN or TRADIER_ACCOUNT_ID environment variables."
            )
        self.base_url = "https://sandbox.tradier.com/v1" if sandbox else "https://api.tradier.com/v1"
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }

    def _get(self, path, params=None):
        r = requests.get(f"{self.base_url}{path}", headers=self.headers, params=params, timeout=15)
        r.raise_for_status()
        return r.json()

    def _post(self, path, data):
        headers = dict(self.headers)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        r = requests.post(f"{self.base_url}{path}", headers=headers, data=data, timeout=15)
        r.raise_for_status()
        return r.json()

    # --- Market data ---

    def get_quote(self, symbol):
        data = self._get("/markets/quotes", {"symbols": symbol})
        quotes = data.get("quotes")
        if not quotes or quotes == "null":
            return None
        q = quotes.get("quote")
        if isinstance(q, list):
            q = q[0] if q else None
        return q

    def get_expirations(self, symbol):
        data = self._get("/markets/options/expirations", {"symbol": symbol})
        exps = data.get("expirations")
        if not exps or exps == "null":
            return []
        dates = exps.get("date")
        if dates is None:
            return []
        return dates if isinstance(dates, list) else [dates]

    def get_chain(self, symbol, expiration):
        data = self._get(
            "/markets/options/chains",
            {"symbol": symbol, "expiration": expiration, "greeks": "true"},
        )
        options = data.get("options")
        if not options or options == "null":
            return []
        opts = options.get("option")
        if opts is None:
            return []
        return opts if isinstance(opts, list) else [opts]

    def get_history(self, symbol, interval="daily", start=None, end=None):
        params = {"symbol": symbol, "interval": interval}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        return self._get("/markets/history", params)

    def search(self, keyword):
        """Market Search — search for a symbol by keyword or partial ticker."""
        return self._get("/markets/search", {"q": keyword})

    def lookup(self, keyword):
        """Lookup Symbols — similar to search, slightly different matching."""
        return self._get("/markets/lookup", {"q": keyword})

    def get_balances(self):
        data = self._get(f"/accounts/{self.account_id}/balances")
        return data.get("balances", data)

    def get_orders(self):
        data = self._get(f"/accounts/{self.account_id}/orders")
        orders = data.get("orders")
        if not orders or orders == "null":
            return []
        o = orders.get("order")
        return o if isinstance(o, list) else ([o] if o else [])

    # --- Trading ---

    def preview_multileg_order(self, symbol, legs, order_type="credit", price=None, duration="day", tag=None):
        return self._submit_multileg(symbol, legs, order_type, price, duration, tag, preview=True)

    def place_multileg_order(self, symbol, legs, order_type="credit", price=None, duration="day", tag=None):
        return self._submit_multileg(symbol, legs, order_type, price, duration, tag, preview=False)

    def _submit_multileg(self, symbol, legs, order_type, price, duration, tag, preview):
        if len(legs) > 4:
            raise ValueError("Tradier multileg orders support a maximum of 4 legs")
        data = {
            "class": "multileg",
            "symbol": symbol,
            "type": order_type,       # market | debit | credit | even
            "duration": duration,     # day | gtc | pre | post
        }
        if price is not None:
            data["price"] = f"{price:.2f}"
        if tag:
            data["tag"] = tag
        if preview:
            data["preview"] = "true"
        for i, leg in enumerate(legs):
            data[f"option_symbol[{i}]"] = leg["option_symbol"]
            data[f"side[{i}]"] = leg["side"]      # buy_to_open/close, sell_to_open/close
            data[f"quantity[{i}]"] = str(leg.get("quantity", 1))
        return self._post(f"/accounts/{self.account_id}/orders", data)

    def get_order(self, order_id):
        data = self._get(f"/accounts/{self.account_id}/orders/{order_id}")
        return data.get("order")

    def get_positions(self):
        data = self._get(f"/accounts/{self.account_id}/positions")
        pos = data.get("positions")
        if not pos or pos == "null":
            return []
        p = pos.get("position")
        if p is None:
            return []
        return p if isinstance(p, list) else [p]
