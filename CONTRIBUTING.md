# Contributing

Thanks for wanting to help. This file is the short version of how work happens
here; the long version lives in `AGENTS.md`, which is worth reading in full —
it is the project's engineering log, kept honest, and most review feedback you
would get is already written down in it.

## Licence, first

The project is **GPL-3.0-or-later** (see `LICENSE` and `docs/LICENSING.md` for
why it cannot be anything else). By submitting a change you agree it is
licensed the same way. There is no CLA.

The bundled game data (`backend/data/*.json.gz`, icons, map textures) is
extracted from Palworld and remains **Pocketpair's copyright** — see `NOTICE`.
The GPL covers this project's code, not their data.

## Setup and tests

```bash
./scripts/setup-dev.sh                           # one time; needs refs/ (see below)
.venv/bin/python -m pytest -m "not integration"  # backend unit: ~3 min
npm test                                         # frontend: <1 s
npm run build && npm run test:e2e                # browser smoke: ~10 s once built
npm run lint && npx tsc --noEmit
```

You will not have `refs/` (a local game-server install) or `refworld/` (a real
world save). **That is fine and expected**: integration tests skip themselves
when either is absent, and CI runs exactly the same subset you can. Changes to
save-writing paths get their integration coverage run by a maintainer before
merge.

## The rules that get PRs declined

Each of these exists because its violation shipped a real bug; `AGENTS.md` has
the receipts.

1. **Never write to a save file unless the server is provably stopped.** All
   mutations go through `backup.guarded_save_write`. Do not add a new path to
   a save; extend an existing writer.
2. **No hand-written or scraped game data.** If the game states a number, it is
   extracted by a script in `scripts/` and bundled with a
   `backend/data/provenance.json` entry. If no file states it, the honest
   answer is a labelled absence, not a wiki figure. (The two standing
   exceptions — the element chart, the staple list — are documented where they
   live.)
3. **No runtime network dependencies.** The container must work offline on a
   LAN. Anything external is fetched once, at build time, and bundled.
4. **A new route needs both gates**: `authz.require(...)` in the handler *and*
   an allowlist entry in `src/lib/permissions.ts`. `test_route_gates.py`
   enumerates the app and will fail you on the first half; nothing can catch a
   missing allowlist entry but review.
5. **Mutating actions call `audit.record`.**
6. **Claims are measured.** "The table has no such column" means you enumerated
   the columns, and the commit message says so. A plausible reading of a name
   is not evidence — this project has retracted enough of those to fill a file,
   and it did (`AGENTS.md`).
7. **User-facing strings go through `t()`** (or `tl()` for labels in data
   arrays), and machine translations stay labelled — see `docs/TRANSLATING.md`.

## What never enters the repo

- `refworld/`, `refs/`, any `*.sav`, any `PalWorldSettings.ini` — real saves
  carry real Steam IDs; the server INI carries live passwords. All are
  gitignored; do not work around it.
- Screenshots or fixtures containing player names or Steam IDs.

## PRs

- Small and focused beats large and mixed. A fix and a refactor are two PRs.
- Commit messages are [Conventional Commits](https://www.conventionalcommits.org):
  `type(scope): summary`, summary in lowercase, then a body that explains
  *why* in sentences — `fix(backup): refuse a corrupt archive instead of
  crashing`. Types: `feat`, `fix`, `docs`, `ci`, `chore`, `refactor`, `test`,
  `perf`, `build`, `revert`. commitlint checks every commit on a PR;
  `npx commitlint --from origin/main` runs the same check locally. Commits
  before 2026-08-25 predate the rule and are not the template.
- If you changed behaviour, a test pins it. If you fixed a bug, the test fails
  without the fix.
- CI (commit messages, backend unit, frontend lint/unit/build, browser smoke,
  Lighthouse, Docker build) must be green.

## Translations

Human verification of a machine-translated language is one of the most useful
contributions possible and needs no code at all: `docs/TRANSLATING.md`.
