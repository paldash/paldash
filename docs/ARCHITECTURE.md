# Architecture

How this codebase is put together and why it is shaped this way. For *what it
does*, read `docs/FEATURES.md`; for *what is left*, `docs/AUDIT.md`.

Measured 2026-08-17: **35,773 lines of backend Python** across 67 flat modules,
**28,294 lines of backend tests**, **27,236 lines of TypeScript** across 45
components, **149 backend routes**. (Re-count when you change this header —
the 2026-07-30 figures sat here at half these values for two weeks.)

---

## 1. Two processes, one container

```
                      ┌─────────────────── container ───────────────────┐
   browser ──:3000──▶ │  Next.js 16 (App Router)                        │
                      │    · the only listener on the network           │
                      │    · src/app/api/  proxies to the backend       │
                      │    · src/lib/permissions.ts  route allowlist    │
                      │              │                                  │
                      │              ├──127.0.0.1:8400──▶ FastAPI       │
                      │              │                     · save parse │
                      │              │                     · save write │
                      │              │                     · SQLite     │
                      │              │                                  │
                      └──────────────┼──────────────────────────────────┘
                                     │
                                     ├──▶ game REST API   (http://palworld:8212)
                                     └──▶ shared bind mount  /palworld
```

**Why two runtimes rather than one.** The only libraries that can read Palworld
1.0's Oodle-compressed saves are Python (`palsav` + `palooz`); the UI wants to be
a modern React app. Rewriting either side in the other's language is a much
larger job than running both.

**Why one container rather than two.** They share a bind mount, a cache
directory and a lifecycle, and the backend must never be network-reachable.
Splitting them would mean publishing a second port or building a private network
purely to re-hide it.

**Next.js is the only thing listening.** The backend binds loopback. That is
defence in depth, not the security boundary — see §4.

---

## 2. The request path

A request for save-derived data crosses four gates before it reaches a file:

| Step | Where | What it decides |
|---|---|---|
| 1 | `src/app/api/save/[...path]/route.ts` | Is this route on the allowlist at all? |
| 2 | `src/lib/permissions.ts` | Does this method on this path need a capability? |
| 3 | `backend/authz.py` | Who is this, really — resolved from SQLite, not from a header |
| 4 | `backend/roles.py` ∩ `backend/policy.py` | Does the role grant it *and* the security level permit it? |

Steps 1–2 are in the proxy and can be bypassed by anything that reaches the
backend directly. Steps 3–4 are in the backend and cannot. The split is
deliberate and load-bearing: **the proxy forwards a credential, it does not
assert an identity.** A forged `X-Actor-Role` header does nothing.

The allowlist is **not a prefix match with a default**. Anything not explicitly
listed is refused, and traversal is rejected before matching. Adding a backend
route without adding it there leaves it unreachable — which is the correct
failure direction.

---

## 3. Backend module map

Flat, and they import each other directly. At 67 modules that is still fine;
the discipline that keeps it fine is that **each module has one job and the
dangerous ones are separated from the safe ones on purpose.**

### Reading the world
| Module | Job |
|---|---|
| `parser.py` | GVAS → dicts. Owns the field-shape helpers (`_num`, `_slot`, `_v`) |
| `savefiles.py` | Path resolution, torn-read guard, atomic write, player-save index |
| `savecache.py` | Parse generation counter, load-aware throttling, disk-cache **schema check** |
| `parse_worker.py` | The parse itself, in a niced subprocess with a timeout. Owns `SCHEMA_VERSION` |
| `viewcache.py` | Memoises derived views; keyed on the parse generation or a file stamp, never a clock |
| `gamedata.py` | Internal id → what players see, case-insensitively |

### Not corrupting the world
| Module | Job |
|---|---|
| `safety.py` | Four independent signals; **fails closed** |
| `backup.py` | `guarded_save_write` — the only way to a write |
| `backupstore.py` | Verified `.tar.gz` archives, retention, restore |

### Writing
Every writer routes through `guarded_save_write`, and the ones that *look* like
new write paths are translations into existing ones:

```
saveedit.py     sort           ─┐
saveimport.py   container      ─┤
slotedit.py     slot patch  ───┘  → builds an import document
charedit.py     Pal / player  ─┐
palcheck.py     repair      ───┤  → produces values, calls apply_pal_batch
palimport.py    Pal document ──┘
palclone.py     create             the only code that *adds* records
teleport.py     position           player .sav, not Level.sav
soloexport.py   uid remap          writes a COPY; never the live world
```

`saveexport.py` has **no write path at all** and lives apart from
`saveimport.py` so the risky code is never one typo from the safe code.

### Writing, the additions since 07-30
`dynamicitem.py` (durability), `itemclone.py` (the only module adding a
`DynamicItemSaveData` record), `guildedit.py` (the four-structure guild move),
`exportscope.py` (the prune option).

### Serving
`main.py` (149 routes), `accounts.py`, `authz.py`, `roles.py`, `policy.py`,
`audit.py`, `db.py`, `privacy.py`, `baseprivacy.py`, `reports.py`,
`worldobjects.py`, `breeding.py`, `editschema.py`.

### Understanding the game (reference data, no save writes)
The post-phase layer — each a separate module because the neighbouring one
holds an *opposite policy on the same data* (the `palstats`/`palresist`/
`workassign` lesson, three times over):

| Module | Job |
|---|---|
| `palstats.py` | the community stat formula, transcribed and labelled `calculated` |
| `palresist.py` | elemental resistance from effect types, never prose |
| `passiveeffects.py` | the 208 effect types, structured |
| `condenser.py` | star → work-suitability bonus (the determined 76%) |
| `buildplanner.py` | whole-species rankings at a chosen build |
| `optimise.py` | roster rankings — matchup is a badge, never a sort key |
| `workrank.py` / `workassign.py` / `baseassign.py` | rank curves · who IS working · who SHOULD |
| `crafting.py` / `itemsource.py` | recursive tree · where an item comes from |
| `elements.py` | the one hand-entered constant, quarantined |
| `habitats.py`, `bossplanner.py`, `completion.py`, `achievements.py`, `progresscheck.py`, `labresearch.py`, `basesupply.py`, `worldclock.py`, `settingshelp.py` | one question each; names say which |

### Talking to the server
`gameapi.py` (the backend's own REST client), `moderate.py` (commands + audit),
`lifecycle.py` (container start/stop), `settings_ini.py`, `iniwatch.py` (did a
write survive the restart), `metrics.py`, `schedule.py`, `announcements.py`,
`gameversion.py`, `mods.py`.

---

## 4. The three invariants

Almost every design decision in this codebase falls out of one of these.

### 4.1 Never write to a save unless the server is provably stopped

A corrupted world is unrecoverable and is the one failure mode that costs the
user something irreplaceable. So `safety.py` **fails closed**: REST, TCP and
save-file mtime must *all* positively say stopped. Unreachable API, unmounted
volume, wrong password, HTTP 401 — every ambiguity resolves to "running".

`guarded_save_write` then re-checks, takes a verified backup, re-checks again,
yields, re-reads from disk, verifies, and rolls back on any mismatch.

**One deliberate exception, in the opposite direction.** `savecache.load_verdict`
fails *open* — no data, a stale sample or an unreachable server all read as
"fine to parse". Refusing to write destroys nothing; refusing to parse forever
merely breaks the dashboard. The asymmetry is the point.

**One module escapes the rule entirely.** `soloexport.py` reads the live world
and writes a *new directory*, so it cannot corrupt anything and does not require
the server stopped.

### 4.2 Two gates must agree before anything is written

A **role** grants a capability (`roles.py`); a **security level** permits it
(`policy.py`, where environment variables are a ceiling the web UI cannot
raise). An Owner on a `safe` server cannot edit saves. That dial protects the
world from mistakes, not from untrusted people — the role gate does that.

### 4.3 Privacy applies to peers and below, never upward

`hidden ⟺ viewer_rank <= hider_rank`. One comparison, and it means a player can
never hide from staff (so moderation needs no exemption list) while equal ranks
*are* concealed (peers being exactly who a privacy setting is for).

The default is **the most private mode**. Nobody should have to discover a
privacy setting exists before they stop being exposed.

Filtering happens in **two** places, because save-derived positions and live
positions arrive by different routes. A filter in only one leaves a hidden
player gone from the map and still showing as a live dot on the same screen.

---

## 5. Frontend

Next.js 16 App Router, one page with tabbed panels, Leaflet for the map,
`recharts` for metrics. Flat dark theme, system fonts, no blocking webfont.

Two structural notes:

- **`src/app/api/` is a proxy, not an API.** It forwards to the backend with the
  session token attached and enforces the allowlist. Business logic lives in
  Python.
- **The editor renders itself from the backend schema.** `editschema.py` serves
  bounds; `character-editor.tsx` builds its form from them. A future level-cap
  change needs no UI edit, and there is no second copy of the rules to drift.

---

## 6. Bundled data

Everything the dashboard knows about Palworld ships in the repo. **There is no
runtime dependency on any external API** — the container must work offline on a
LAN.

| File | Size | Contents |
|---|---:|---|
| `gamedata.json.gz` | 215 KB | 2,466 items, 753 Pals, 1,905 passives, 588 techs, 174 fast-travel points |
| `worldobjects.json.gz` | 717 KB | 51,921 static objects — ore, spawners, chests, dungeons, fishing, oil, NPCs |
| `pal_breeding.json.gz` | 253 KB | Full combi table |
| `effigies.json.gz` | 15 KB | 396 effigies **with the GUIDs saves key on** |
| `pal_db.json.gz` | 29 KB | Per-Pal stats |
| `server_defaults.json` | 4 KB | The 119 authoritative INI settings |
| `provenance.json` | 2 KB | Where each of the above came from, and how to regenerate it |
| `public/maps/*.webp` | 4.3 MB | Both 8192 px landmass textures |

Generated by `scripts/build-*.py` and `scripts/extract-*.py` out of `refs/`,
which is gitignored (~5.1 GB: third-party archives plus a dedicated-server
install). Regeneration is needed only on a new Palworld release.

**`provenance.json` is checked by a test**, so a bundle added without recording
where it came from fails the suite.

---

## 7. Testing

**`docs/TESTING.md` is the full guide** — how to run everything, what pins
what, and the traps. The short form:

| Command | Scope | Time |
|---|---|---|
| `pytest -m "not integration"` | Backend unit (~1,900 tests) | ~3 min |
| `pytest` | Everything, against a real world (2,043) | ~25 min |
| `npm test` | Frontend (vitest, 153) | ~1 s |

Integration tests skip automatically when `refworld/` or `palsav` is absent, so
a clean checkout still runs green. **Clear `/tmp/pytest-of-$USER` before a full
run** — 2.6 GB per run on a tmpfs, and a full `/tmp` presents as every shell
command failing with no output, not as a disk error.

**The pattern worth knowing**: findings get pinned by a test, not just a
comment. `group_id_belong_to` is the guild rather than the base; the `max()` in
the stack ceiling; `unknown_species` being advisory; the ordering of the load
check before any filesystem access. Each is a test whose name is the claim.

Two testing hazards this project has actually hit:

- **`vitest.config.ts` excludes `.next/`.** `next build` copies `src/` into
  `.next/standalone/`, and vitest was discovering the stale copy — which would
  stay green against yesterday's build while the real source failed.
- **Module-level constants capture environment variables at import time.** Tests
  monkeypatch the module attribute, not `os.environ`. Patching `os.environ`
  passes for the wrong reason, and patching `safety.assert_writable` does
  nothing to `backup.py`, which bound the name at import.

---

## 8. Where the bodies are buried

Things that are true, non-obvious, and have already caused a bug:

- **`Level` and `Talent_*` are ByteProperty** and nest one level deeper than
  Int. Writing at the wrong depth produces a file that serialises, loads, and
  silently ignored the edit.
- **`PassiveSkillList` and `EquipWaza` are ArrayProperty** — values at
  `node["value"]["values"]`, and `array_type` must survive untouched.
- **`palsav` decodes GUIDs as its own `UUID` class, not `str`.** An
  `isinstance(v, str)` test matches nothing; a first version of `soloexport`
  counted 6,455 uid fields and rewrote zero of them.
- **`group_id_belong_to` is the guild, not the base.** Both are GUIDs sitting
  beside each other in the same `RawData`.
- **The map is two regions with separate framings**, not one image. 157/157
  Palpagos fast-travel points land correctly; 0/17 World Tree points do.
- **In-game map X derives from world Y**, and map Y from world X.
- **There are no empty character-container slots.** `SlotNum` is capacity; the
  array holds only occupied slots, so adding a Pal appends.
- **An unrecognised character id is not evidence of cheating.**
  `CharacterSaveParameterMap` holds humans too, and the bundled tables are
  incomplete.
- **EXP-vs-level is one-sided.** 0 of 1,905 Pals sit above their band; 8 sit
  below. Low EXP is a state the game itself produces.
- **`output: "standalone"` needs `outputFileTracingExcludes`.** Without it the
  tracer copied `refs/` and `refworld/` into `.next/standalone/` — 5.8 GB of
  build output for a 77 MB app, including live server passwords and a real save.

---

## 9. Conventions

- Comments explain **why**, especially where a subtlety already caused a bug.
- Backend modules are flat and import each other directly.
- New backend storage goes in `backend/db.py` (SQLite). The Python process owns
  that file exclusively; Next.js asks over loopback rather than opening a second
  driver.
- Every mutating action gets an `audit.record` call.
- Every new backend route gets an entry in `src/lib/permissions.ts`.
