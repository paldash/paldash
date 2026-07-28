#!/usr/bin/env bash
# Build the local development environment.
#
# palsav/palooz is a compiled Oodle (Kraken) extension that is not on PyPI. The
# archive in refs/ vendors the `ooz` sources, so this needs no network access
# beyond pip — a plain `pip install git+...` would fail, because it does not
# fetch the submodule the build depends on.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"
ARCHIVE="$ROOT/refs/PalWorldSaveTools-main.zip"
VENV="$ROOT/.venv"

echo "==> Creating virtualenv"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip

echo "==> Installing backend + test dependencies"
"$VENV/bin/pip" install --quiet \
    -r "$ROOT/backend/requirements.txt" \
    -r "$ROOT/backend/requirements-dev.txt"

if [ ! -f "$ARCHIVE" ]; then
    echo
    echo "!! $ARCHIVE not found."
    echo "   Skipping palsav. Unit tests will run; integration tests will skip."
    echo "   Get it from https://github.com/deafdudecomputers/PalworldSaveTools"
    exit 0
fi

echo "==> Extracting palsav from refs/"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
unzip -q "$ARCHIVE" -d "$WORK"

PALSAV="$(dirname "$(find "$WORK" -type d -name palooz -path '*/palsav/*' | head -1)")"
if [ -z "$PALSAV" ] || [ ! -d "$PALSAV" ]; then
    echo "!! Could not locate src/palsav inside the archive." >&2
    exit 1
fi

echo "==> Building palooz (C++ Oodle decoder) — this takes a minute"
"$VENV/bin/pip" install --quiet "$PALSAV/palooz"
"$VENV/bin/pip" install --quiet "$PALSAV"

echo "==> Verifying"
"$VENV/bin/python" - <<'PY'
import fastapi, pytest, palsav, palooz
print("  fastapi, pytest, palsav, palooz all import cleanly")
PY

echo
echo "Done. Run the tests with:"
echo "    .venv/bin/python -m pytest"
