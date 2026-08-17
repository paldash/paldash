# Upgrading — the dashboard, and Palworld underneath it

Two different things update, and they need nothing from each other.

## 1. Updating the dashboard itself

Rebuild the image, redeploy, done. Open tabs are told a new build shipped
(`version-banner`), and the parse cache carries a schema version, so a payload
written by an older dashboard is discarded and rebuilt rather than misread —
including the automatic re-parse (`recover_stale_schema`), so nobody has to
press Refresh after an upgrade.

## 2. When Palworld updates — running the dashboard (operator)

**Nothing breaks and there is nothing you must do.** Every bundled fact was
extracted from the build it names and does not go wrong retroactively; what a
game update can add is *new* content the dashboard does not know names or
positions for yet. The in-app banner ("Palworld updated — some new content may
not be named yet") says exactly this, and hides the details behind a toggle
because they are the maintainer's job, not yours.

The dashboard detects the update itself: `gameversion.py` polls the install's
Steam `appmanifest` for its `buildid` (two file reads, no network), always once
at startup — the common case is a container that auto-updated the game and
restarted (`AUTO_UPDATE_ENABLED` on thijsvanloef).

## 3. When Palworld updates — refreshing the bundled data (maintainer)

Needs `refs/` (the dedicated-server install and the client pak), which is not
shipped. In order, and the order is the lesson:

### Step 0 — update the reference install FIRST

**A diff against the pak already on disk proves nothing about a new build** —
it re-derives the bundles from the same bytes they were built from and can only
ever report "unchanged". This mistake was made in this repository and corrected
by the operator; do not repeat it.

```bash
steamcmd +force_install_dir "$(pwd)/refs/palworld" \
         +login anonymous +app_update 2394010 validate +quit
```

For the client-pak-derived bundles (display names, 16 languages, icons), also
copy a fresh `Pal-Windows.pak` from an updated Steam *client* install into
`refs/` — the server depot does not carry it.

### Step 1 — see what actually changed

```bash
.venv/bin/python scripts/check-game-build.py            # build ids, instant
.venv/bin/python scripts/check-game-build.py --extract  # re-derive + diff, ~minutes
```

The diff is per object, position-rounded — so "the file changed" and "one rock
moved" are different answers. `--write` updates `worldobjects.json.gz` and
refuses if any populated category comes back empty, because a patch does not
delete a whole category and leave its neighbours byte-identical.

Note the converse case is real too: **a build can ship with no pak change at
all.** 24466863 updated only the server binary — the pak stayed byte-identical
to 24370498's — so stamps moved and no bundle did.

### Step 2 — regenerate everything

```bash
.venv/bin/python scripts/regenerate-bundles.py
```

This is the runbook as a script: it runs every `regenerateWith` command from
`backend/data/provenance.json` (a bundle joins the procedure by being
documented), in dependency order, and prints **changed / unchanged / failed**.
`jsonout.py` writes with `mtime=0`, so unchanged input is byte-identical and
`git status` names exactly what the update touched.

**A failure is a refusal doing its job, not a crash to retry.** The boss
spawners refuse if a position falls off the cell grid; the settings CDO refuses
if `CharacterMaxLevel`/`CharacterMaxRank` stop matching; `build-habitats.py`
refuses if any species loses its habitat with no base form covering it; the
movement-mode extractor refuses if its swimmer/land-variant controls stop
disagreeing. Read the message — it is the news.

### Step 3 — stamps, tests, review

1. Update `gameBuild` in `backend/data/provenance.json` for what was
   regenerated (hand-edited; the extractors do not write it).
2. `.venv/bin/python -m pytest -m "not integration"` — many tests pin the
   *shipped bundles*, so a regression in a regeneration fails here, not in
   production.
3. Full suite before calling it done: `.venv/bin/python -m pytest`
   (clear `/tmp/pytest-of-$USER` first; a full `/tmp` presents as every shell
   command failing with no output).
4. Review `git diff --stat backend/data/` and commit. A changed bundle after a
   game update is expected; a changed bundle **without** one is an extractor
   bug, because identical input must produce identical bytes.

### Things a patch can invalidate that no script checks

- **Map calibration.** The World Tree transform is provisional
  (`calibrated: false`) and the Palpagos one is fitted to 174 fast-travel
  points; a patch that adds streaming cells or a landmass shows up in the cell
  grid and may move the extents `map-coordinates.ts` uses.
- **The element chart** (`elements.py`) is the one hand-entered constant. A
  content update adding an element makes `unknown_to_chart()` non-empty, which
  the tests watch — but the *relation* for a new element needs a human.
- **The stat formula** (`palstats.py`) is a transcription; diff against the
  PST implementation if a patch moves a number.
