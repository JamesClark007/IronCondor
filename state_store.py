"""
Tiny JSON-file state store. GitHub Actions runs are stateless (fresh container
each time), so we persist position state to a file in the repo and have the
workflow commit it back after each run — that's how entry.py "remembers" what
manage.py needs to check five minutes later.
"""
import json
import os
from datetime import date

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")


def load_state():
    if not os.path.exists(STATE_PATH):
        return {"date": None, "status": "flat", "position": None}
    with open(STATE_PATH) as f:
        return json.load(f)


def save_state(state):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2, default=str)


def new_day_reset_if_needed(state):
    """If the state file is from a previous day, reset to flat for a fresh session."""
    today = date.today().isoformat()
    if state.get("date") != today:
        return {"date": today, "status": "flat", "position": None}
    return state
