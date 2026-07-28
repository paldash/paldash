# AGENTS.md

Guidance for agents working in this repository. `CLAUDE.md` is a pointer to this file.

## What this is

A web dashboard for a self-hosted Palworld server: a Next.js UI and a Python
save-parsing backend, running as one container beside the game server on a
shared bind mount.

- `src/` — Next.js 16 App Router UI. The API routes under `src/app/api/` are the
  **entire security boundary**; see below.
- `backend/` — FastAPI service that parses and (carefully) mutates save files.
- `docs/AUDIT.md` — current state, gap analysis, and the phased roadmap. Read
  this before planning work.
- `refs/` — third-party reference archives (gitignored, ~66 MB). Contains the
  authoritative Palworld 1.0 game database; see "Reference data" below.
- `refworld/` — a real world save used for integration tests (gitignored,
  contains real Steam IDs and player names — never commit it, never paste its
  contents into an issue).

## Commands

```bash
# Setup (one time). Builds .venv and compiles palsav/palooz out of refs/.
./scripts/setup-dev.sh

# Tests
.venv/bin/python -m pytest                      # everything (~100s)
.venv/bin/python -m pytest -m "not integration"  # unit only, <1s, no save needed
.venv/bin/python -m pytest -m "not slow"         # skip full-world parses
.venv/bin/python -m pytest backend/tests/test_safety.py -k read_only  # one test

# Frontend
npm run dev
npm run lint
npm run build

# Backend alone
.venv/bin/python backend/main.py    # binds 127.0.0.1:8400
```

Integration tests skip automatically when `refworld/` or `palsav` is absent, so
a clean checkout still runs green.

## The rule that matters most

**Never write to a save file unless the server is provably stopped.** A corrupted
world is unrecoverable and is the one failure mode that actually costs users
something irreplaceable.

`backend/safety.py` fails *closed*: it combines four independent signals (REST
API, TCP port, save-file mtime, process scan) and only reports "editable" when
REST, TCP and file activity all positively say stopped. Anything ambiguous —
unreachable API, unmounted volume, wrong password — resolves to "running".
An HTTP 401 counts as **running** (something is listening and rejecting us).

Every mutation goes through `backup.guarded_save_write`, which re-checks safety,
takes a full backup, then re-checks again before yielding. `backend/saveedit.py`
adds the conservation invariant: total quantity of every item in every container
must be identical after a sort, verified in memory *and* again after re-reading
from disk, with automatic rollback on mismatch.

When you touch any of this, run the full suite including `-m slow`. Those tests
exercise the real pipeline against a real world.

## Palworld 1.0 specifics

- Saves use Oodle Kraken with magic bytes `PlM` (previously `PlZ`/zlib). The PyPI
  package `palworld-save-tools` **cannot read them**. Use `palsav` + `palooz`.
- `palsav` is **GPL-3.0-or-later**. Distributing this dashboard publicly would
  make the combined work GPL-3.0. Private/LAN use triggers nothing.
- Field shapes that bite: `Level` and `Talent_*` are ByteProperty and nest one
  level deeper than Int (`{'value': {'value': 24}}`); the field is `SlotId` not
  `SlotID`; item `count` sits at the slot root while `static_id` is nested under
  `item`. `backend/parser.py` has `_num`/`_slot`/`_v` helpers for these — use
  them rather than indexing directly.
- Player `.sav` filenames are uppercase undashed hex; `Level.sav` stores
  lowercase dashed GUIDs. Match via `savefiles.get_player_sav_path`.

## Security boundary

The Python backend has **no authentication** and binds to loopback. The Next.js
proxy routes (`src/app/api/save/`, `src/app/api/palworld/`) are the only thing
separating a guest from the save editor. Treat every change there as
security-critical.

Access control is capability-based (`src/lib/permissions.ts`) with a runtime
policy layer (`backend/policy.py`) where **environment variables are a ceiling**:
`SECURITY_LEVEL=readonly` in compose cannot be raised from the web UI.

Known gaps are catalogued in `docs/AUDIT.md` §5 — there is currently one shared
password, no user accounts, no audit log and no login rate limiting.

## Reference data

`refs/PalWorldSaveTools-main.zip` contains `resources/game_data/` — the
authoritative Palworld 1.0 database (2,466 items, 753 Pals, 1,905 passives, 588
technologies, 174 fast-travel points with coordinates, 2,468 icons, both map
textures). It is MIT-licensed and validated against the reference save.

**Do not hand-write or scrape game data that already exists there.** Do not add a
runtime dependency on any external API — the container must work offline on a LAN,
so anything adopted is fetched once and bundled.

## Conventions

- Comments explain *why*, especially where a subtlety already caused a bug.
  Match the existing density; do not narrate obvious code.
- Backend modules are flat and import each other directly. Module-level constants
  capture environment variables **at import time**, so tests monkeypatch the
  module attribute, not `os.environ`.
- No test framework on the frontend yet. Backend tests are pytest, no plugins.
