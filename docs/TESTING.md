# Testing guide

How to run everything, what each layer pins, and the traps that have actually
fired. `docs/ARCHITECTURE.md` §7 is the summary; this is the working guide.

## The commands

```bash
# Backend — pytest, no plugins
.venv/bin/python -m pytest -m "not integration"   # unit: ~1,900 tests, ~3 min
.venv/bin/python -m pytest -m "not slow"          # skip full-world parses
.venv/bin/python -m pytest                        # everything: 2,043 tests, ~25 min
.venv/bin/python -m pytest backend/tests/test_safety.py -k read_only   # one test

# Frontend — vitest
npm test                                          # 153 tests, ~1 s

# The other gates a change must clear
npx tsc --noEmit
npm run lint                                      # 0 errors expected; warnings are pre-existing
npm run build                                     # catches what tsc alone does not
```

**Before a full run: `rm -rf /tmp/pytest-of-$USER`.** One run leaves ~2.6 GB in
`$TMPDIR` and pytest does not always reclaim it. Four runs wedge a 7.7 GB
tmpfs, and a full `/tmp` presents as **every shell command failing with no
output** — not as a disk error. A wedged run then fails loudly but not
honestly: 211 failures, every one `OSError: Disk quota exceeded`, none a real
defect. Check the *reason* before believing a failure count, then re-run on a
clear disk rather than reasoning your way to a pass.

**Read exit codes, not grep counts.** pytest colours its summary, so
`grep -c "^FAILED"` silently reads 0 through the ANSI escape prefix — this
repository lost one real failure to exactly that during the v1.0.3 refresh.
`$?` is the authority.

## What lives where

| Layer | Where | What it pins |
|---|---|---|
| Backend unit | `backend/tests/test_*.py` | module behaviour, **and the shipped bundles** (see below) |
| Backend integration | same files, `@pytest.mark.integration` | the real pipeline against `refworld/` — a genuine 55 MB world |
| Frontend | `src/**/*.test.ts` | permissions allowlist, item lookup, build config |
| Route gates | `backend/tests/test_route_gates.py` | every live route has `authz.require` AND an allowlist entry |

Integration tests **skip automatically** when `refworld/` or `palsav` is
absent, so a clean public checkout runs green without the private world save.

## The two testing philosophies here, and when each applies

**1. Findings are pinned by a test whose name is the claim.** When something
was measured (`group_id_belong_to` is the guild, not the base; low EXP is
legitimate and high EXP is not; `unknown_species` is advisory), a test states
it, so the next refactor cannot silently unlearn it.

**2. Bundle tests run against the SHIPPED `.json.gz`, never a fixture.**
`test_crafting.py`, `test_gametext.py`, `test_item_legality.py`,
`test_effigy_names.py` and friends load `backend/data/*` from disk. A fixture
would pin the walker and let the bundle regress underneath it. The corollary:
**after a game update, a failing bundle pin is often the game changing, not
the code breaking.** v1.0.3 flipped one item legal and the 575-item legality
pin failed by exactly one — the correct response was to update the pin *with
the story*, not to loosen the assertion.

## A zero needs a positive control

A checker that reports zero violations is indistinguishable from a checker
that can never fire. `test_workassign.py` is the pattern: the measured result
is zero unsuitable assignments, so the test also plants a Melpaca on a
workbench (must be caught) and an Anubis (must not). Any new "scan for
problems" feature needs both controls before its zero means anything.

## Traps that have actually fired

- **Module-level constants capture environment at import time.** Monkeypatch
  the module attribute (`monkeypatch.setattr(db, "DB_PATH", …)`), never
  `os.environ` — the env patch passes for the wrong reason. A test once set
  `DB_PATH` in the environment, which `db.py` does not read (the variable is
  `DASHBOARD_DB`), and eight tests silently shared one database.
- **Tests that write need `fresh_db`/`tmp_path`.** `write_ini` gained a SQLite
  side effect and ten existing tests started leaving rows — including sealed
  password material — in the development database. A test that mutates shared
  state outside its `tmp_path` is one refactor away from doing it somewhere
  worse.
- **`vitest.config.ts` excludes `.next/`** — otherwise vitest discovers the
  stale copy `next build` made and stays green against yesterday's source.
- **A security test's false alarm is worse than no test.** The route-gate
  test found three bugs in itself before any in the code: a regex that parsed
  20 of 107 patterns, a body-only scan that missed helper delegation, and a
  single probe id that failed underscore-excluding character classes. Probe
  multiply, and verify the test can *pass* on known-good routes before
  trusting its failures.
- **Editing source while the suite runs races collection.** A module edited
  after pytest collected its test file can fail assertions from the old file
  against the new module. The standalone re-run of that file is authoritative.

## Verification beyond pytest

These are not tests but are part of "did we break it":

```bash
.venv/bin/python scripts/verify-figures.py       # re-derives measured figures across worlds
.venv/bin/python scripts/extract-game-settings.py --verify   # CDO decode self-check
.venv/bin/python scripts/check-game-build.py --extract       # bundle vs installed pak
```

And the extractors themselves **refuse** rather than emit on a control
failure (boss positions off the cell grid, a species losing its habitat, the
movement-mode controls agreeing). A refusal message is a result; read it.
`docs/UPGRADING.md` sequences all of this for a game update.
