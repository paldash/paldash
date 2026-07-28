#!/bin/sh
# Supervise both processes. If either dies the container exits so Docker's
# restart policy can do its job, instead of leaving a half-dead dashboard up
# (the old version exec'd Next.js and never noticed if the backend died).
set -e

echo "Palworld Dashboard starting..."

BACKEND_PORT="${BACKEND_PORT:-8400}"

# Bind the save backend to loopback only. It has no auth of its own — the
# Next.js layer enforces admin/guest — so it must never be reachable from
# outside the container.
BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
export BACKEND_HOST BACKEND_PORT

cd /app/backend
python3 main.py &
BACKEND_PID=$!
cd /app
echo "  save backend -> ${BACKEND_HOST}:${BACKEND_PORT} (pid ${BACKEND_PID})"

node server.js &
WEB_PID=$!
echo "  dashboard    -> 0.0.0.0:3000 (pid ${WEB_PID})"

term() {
    echo "Shutting down..."
    kill -TERM "$BACKEND_PID" "$WEB_PID" 2>/dev/null || true
    wait 2>/dev/null || true
    exit 0
}
trap term TERM INT

# Exit as soon as either child exits.
wait -n "$BACKEND_PID" "$WEB_PID"
EXIT_CODE=$?
echo "A child process exited (code ${EXIT_CODE}); stopping container."
kill -TERM "$BACKEND_PID" "$WEB_PID" 2>/dev/null || true
exit "$EXIT_CODE"
