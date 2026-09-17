#!/usr/bin/env bash
#
# Cron wrapper for the 0DTE bot (Option B — always-on server).
#
#   ./run.sh entry
#   ./run.sh manage
#   ./run.sh report --days 90 --snapshot
#   ./run.sh check_positions
#
# Loads credentials from .env, holds a per-script lock so two runs can never
# overlap, and appends stdout+stderr to a dated log file.
#
# Install:
#   chmod +x run.sh
#   mkdir -p logs
#   edit BOT_DIR below if the bot does not live in /opt/0dte/bot

set -euo pipefail

BOT_DIR="${BOT_DIR:-/opt/0dte/bot}"
cd "$BOT_DIR"

if [ $# -lt 1 ]; then
  echo "usage: $0 <entry|manage|report|check_positions|find_vix_symbols> [args...]" >&2
  exit 64
fi

SCRIPT="$1"
shift

if [ ! -f "${SCRIPT}.py" ]; then
  echo "No such script: ${SCRIPT}.py" >&2
  exit 66
fi

if [ ! -f .env ]; then
  echo "Missing .env — create it with TRADIER_TOKEN and TRADIER_ACCOUNT_ID (chmod 600)." >&2
  exit 78
fi

# shellcheck disable=SC1091
set -a; source .env; set +a

mkdir -p logs

# -n: if a previous run of this same script is still going, exit rather than
# queue. For entry.py this is what stops a second condor being opened.
exec flock -n "/tmp/0dte-${SCRIPT}.lock" \
  ./venv/bin/python "${SCRIPT}.py" "$@" \
  >> "logs/${SCRIPT}-$(date +%F).log" 2>&1
