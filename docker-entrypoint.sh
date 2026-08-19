#!/bin/bash
# Supervise both processes. If either dies the container exits so Docker's
# restart policy can do its job, instead of leaving a half-dead dashboard up
# (the old version exec'd Next.js and never noticed if the backend died).
#
# bash, NOT sh. `wait -n` below is a bashism and Debian's /bin/sh is dash, which
# fails with "wait: Illegal option -n" — and because of `set -e` that killed the
# container a second after boot, every time. Do not "simplify" this back to sh.
set -e

echo "Palworld Dashboard starting..."

# ── Self-provisioned state from previous boots (#149) ──
# The backend regenerates stale bundles and fetches artwork into the cache
# volume; the image's own copies are ephemeral, so each boot overlays what
# earlier boots produced BEFORE anything imports it. Copy, not symlink: the
# bundled files must keep working when the volume is empty.
CACHE_DIR="${CACHE_DIR:-/app/cache}"
if [ -d "${CACHE_DIR}/provision/bundles" ]; then
    n=$(cp -fv "${CACHE_DIR}/provision/bundles/"* /app/backend/data/ 2>/dev/null | wc -l)
    [ "$n" -gt 0 ] && echo "  overlaid ${n} refreshed data bundle(s) from the cache volume"
fi
for kind in icons maps; do
    if [ -d "${CACHE_DIR}/provision/public-${kind}" ] && [ -n "$(ls -A "${CACHE_DIR}/provision/public-${kind}" 2>/dev/null)" ]; then
        mkdir -p "/app/public/${kind}"
        cp -rf "${CACHE_DIR}/provision/public-${kind}/." "/app/public/${kind}/"
        echo "  restored game ${kind} from the cache volume"
    fi
done

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
