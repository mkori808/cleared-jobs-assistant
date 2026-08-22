#!/usr/bin/env bash
# Single-command startup for local dev or the containerized deploy.
#   ./start.sh            -> runs uvicorn directly (local dev, conda env)
#   ./start.sh --docker   -> runs `docker compose up --build` instead
#   ./start.sh --service  -> runs uvicorn under a restart-on-crash loop, logging to
#                            service.log (used by the Task Scheduler entry that launches
#                            this at logon so the app survives crashes/reboots unattended)
set -euo pipefail
# Resolve to an absolute path up front: $0 can be a bare relative name with no directory
# component (e.g. just "start.sh", as Task Scheduler invokes it), which breaks re-invoking
# "$0" later on since a bare filename with no "./" prefix isn't found via PATH lookup.
SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
cd "$(dirname "$SCRIPT_PATH")"

if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

if [ "${1:-}" = "--docker" ]; then
  exec docker compose up --build
fi

if [ "${1:-}" = "--service" ]; then
  # If something (a manual `start.sh` run, or this task firing twice) already has the port,
  # don't crash-loop fighting over the bind -- log it and exit cleanly instead.
  if netstat -ano 2>/dev/null | grep -q ":${PORT:-8000} .*LISTENING"; then
    echo "$(date -Is) already listening on port ${PORT:-8000} -- not starting a duplicate." >> service.log
    exit 0
  fi
  while true; do
    echo "$(date -Is) starting" >> service.log
    # Wrapped in `if` so a non-zero exit is a normal branch, not a `set -e` abort of this
    # whole supervisor loop -- a crash is exactly the case this loop exists to survive.
    if "$SCRIPT_PATH" >> service.log 2>&1; then code=0; else code=$?; fi
    echo "$(date -Is) exited (code $code) -- restarting in 10s" >> service.log
    sleep 10
  done
fi

# A stray user-level PYTHONPATH on this machine points at the Windows Store
# Python's site-packages and contaminates every other interpreter (including
# conda envs), causing ABI-mismatched native modules (e.g. pydantic_core) to
# load. Unset it for this process so the right env's packages are used.
unset PYTHONPATH

if [ -x "$HOME/Anaconda3/envs/general/python.exe" ]; then
  PYTHON="$HOME/Anaconda3/envs/general/python.exe"
elif [ -n "${CONDA_PREFIX:-}" ]; then
  PYTHON="$CONDA_PREFIX/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="python3"
else
  PYTHON="python"
fi

"$PYTHON" -m pip install -q -r requirements.txt

# Defaults to 0.0.0.0 (every interface on this machine, including whatever
# network you're on) for convenience during local dev. Set BIND_HOST to the
# WireGuard interface's IP (e.g. `BIND_HOST=10.77.77.1 ./start.sh`, or put it
# in .env) to make the server unreachable from any other network the machine
# joins, including public wifi.
HOST="${BIND_HOST:-0.0.0.0}"
if [ "$HOST" = "0.0.0.0" ]; then
  echo "WARNING: binding to 0.0.0.0 - reachable from ANY network this machine is on (including public wifi)." >&2
  echo "         Set BIND_HOST to your WireGuard interface IP to restrict this." >&2
fi
exec "$PYTHON" -m uvicorn app:app --host "$HOST" --port 8000
