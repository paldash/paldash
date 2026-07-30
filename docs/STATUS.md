# Status

Snapshot: **2026-07-30**. Phases 0–9 complete.

Verification for this snapshot is recorded in §6.

---

## 1. What works, end to end

### Reading the world
Parses Palworld 1.0 Oodle (`PlM`) saves that the usual Python tooling cannot
read at all. From a real 2.0 MB world: 1,905 Pals, 5 players, 11 bases, 6
guilds, 11,639 containers, 8.3 M items, 3,370 placed objects.

Friendly names for everything through a bundled 215 KB database — 2,466 items,
753 Pals, 1,905 passives, 375 active skills, 588 technologies. Lookups are
case-insensitive on purpose: the upstream data spells things three different
ways and exact matching silently loses eight real Pals.

### Not corrupting the world
The rule the whole project is built around: **never write unless the server is
provably stopped.** Four independent signals (REST API, TCP port, save-file
mtime, process scan) must agree, and anything ambiguous resolves to "running".
An HTTP 401 counts as running — something is listening.

Every mutation goes through `guarded_save_write`: re-check, full verified
backup, re-check, write, **re-read from disk**, verify, automatic rollback on
any mismatch. Sorting additionally proves conservation — every item total
identical before and after, checked twice.

### Editing (Phase 7, complete)
| Feature | Guarantee |
|---|---|
| Container sorting | Item totals conserved, verified after re-read |
| Slot editing | Target container matches plan, **every other container unchanged** |
| Pal editor | Per-field bounds from game data; absent properties refused, not invented |
| Skills | Passives (≤4) and equipped moves (≤3), ids checked against 1,905/375 |
| Bulk Pal edits | All-or-nothing across the batch |
| Pal cloning | Both records created and paired; no other container may change length |
| Player editor | Spans two files; either mismatch rolls back the whole world |
| Illegal-Pal check | Scans against the same schema the editor enforces |
| Import / export | Versioned, checksummed; `planHash` refuses a world that moved |
| Pal import | Same envelope as the export; unwritable fields listed, never dropped |
| Coordinate teleport | Player `.sav` only; server must be stopped, verified on re-read |

### Access control
Seven roles, two independent gates (role capability ∩ security level), a route
allowlist that is not a prefix match, scrypt password hashing, server-side
revocable sessions, per-IP and per-username throttling, and an audit record on
every mutating action.

### Server operations (Phase 8)
Kick, ban, unban, announce, force-save, graceful shutdown and container
start/stop, all through `moderate.py` so **every one of them is audited** —
including the failures, because an attempt that did not land still says who
tried. CPU/RAM/disk metrics sampled every 60 s and kept 30 days raw.

Recurring announcements ride the existing scheduler tick. An empty server
**consumes** its window rather than queueing, so logging in does not trigger
every overdue message at once.

### Reading the game's own files
The pak is unencrypted and Oodle-compressed, which this project already
decompresses. `scripts/palpak.py` extracts any of its 158,444 files;
`scripts/upackage.py` reads package export maps. Three results the save files
cannot give:

- the World Partition cell grid, which fixed the World Tree map
- all **396 effigies** with the GUIDs saves key on
- **35,687 world objects** — 24,359 ore nodes, 8,386 chests, 2,757 fishing spots,
  185 oil fields — now rendered on the map with viewport culling, per-kind
  toggles, and an admin policy controlling which categories each role may see

Positions are static per game build; the save supplies the *state* (mined,
looted, respawning) and updates on a normal parse refresh.

### Privacy and visibility
Three dials, at decreasing scope:

- **`discoveryVisibility`** (Owner) — who sees map content nobody has found yet.
- **`map_privacy`** (each player, about themselves) — `off`, `player`,
  `player_bases`, `guild`. **Defaults to the most private.**
- **Per-base visibility** (`baseprivacy.py`) — gated on the guild master, with a
  fallback for guilds whose master has no dashboard account. Staff get no
  override; it fails **closed** when no world has been parsed.

The privacy rule is one line: `hidden ⟺ viewer_rank <= hider_rank`. Applied
server-side in both the save endpoints and the REST proxy — live positions come
from the game, not the save, so a filter in only one place would leave a hidden
player showing as a live dot.

### Migration (Phase 9)
`soloexport.py` remaps one player's uid across a **copy** of the world, for
carrying a character between a dedicated server and co-op. It is the only save
feature that is safe to run while the server is up, because it never writes to
the live world.

### Staying current with the game
`gameversion.py` reads the server install's Steam `appmanifest` for its
`buildid` — two file reads and a stat, no network — and the UI banners a build
that has moved past the one the bundled data came from. Build ids are monotonic,
so an update and a rollback are distinguishable. `mods.py` reports whether the
server is modded, which is what lets `palcheck` say whether unrecognised species
have an innocent explanation.

Both report **"cannot tell"** when the game directory is not visible, which is
the normal deployment. Neither ever renders that as "nothing found".

### Request cost
`backend/viewcache.py` memoises the two things the request path was repeating:
views derived from a parse (keyed on the parse generation) and values derived
from a file (keyed on its size and mtime). Nothing is keyed on a clock, and
authorisation and privacy decisions are deliberately excluded.

| Path | Before | After |
|---|---:|---:|
| `get_players()`, 5 players | 11,500 µs | 34 µs |
| `/api/pals`, 1,905 Pals | 12 ms | ~0 |
| `/api/mapobjects`, 3,370 objects | 10 ms | ~0 |
| Privacy filter, 20 accounts | 60 µs | *not cached, on purpose* |

---

## 2. Numbers worth keeping

Every one of these is measured against real data, not quoted.

| Fact | Value | How it was established |
|---|---:|---|
| Streaming cell size | 25,600 | 174/174 fast-travel points land on an occupied cell |
| Effigies | 396 | Package export map; 37/37 collected ones verified against a save |
| Static world objects | 35,687 | Pak extraction; bundled at 486 KB |
| Fast-travel points | 174 | Bundled data; joins to saves 117/117, 92/92, 11/11, 78/78, 64/64 |
| Level cap | 80 | 1.0 raised it from 65. **Not** the 100 rows in `palExpTable` |
| Max equipped moves | 3 | Never exceeded across 1,905 Pals |
| Max passives | 4 | Same |
| IV range | 0–100 | Same |
| Pals above their EXP band | **0** of 1,905 | Which is why that rule is one-sided |
| Pals below their EXP band | 8 | Freshly caught — a state the game itself creates |
| NPCs in the character map | 100 of 1,905 | Why `gamedata.character()` exists |
| Empty character-container slots | **0** | Cloning appends; it cannot fill |
| 1.0 server settings | 119 | `DefaultPalWorldSettings.ini` |
| uid references a key-list remap misses | **1,836** | Counted against the reference world |
| Build output, before/after tracing excludes | 5.8 GB → 73 MB | Measured 2026-07-30 |

### Corrections this project has made to its own claims
Worth reading before trusting any number that is not in the table above.

- **Effigies: 313 → 149 → 396.** 313 was a community figure from the first
  commit, never verified. 149 was mine and also wrong — it counted distinct
  actor *names*, and the package name table dedupes strings many exports share.
- **EXP must match level → must not exceed level.** The symmetric rule looked
  obviously right and would have rejected 8 legitimate Pals.
- **Level cap 100 → 80.** Derived from the EXP table's row count, which carries
  headroom past the cap.
- **`unknown_species` flagged 108 of 1,905 Pals on a clean world.** All false
  positives. It is now an advisory that never counts as a violation.
- **World Tree framing 82% → ~100%.** The old transform assumed the map framed
  the fast-travel bounding box; it frames the landmass.
- **"Both server images always rewrite the INI" → jammsen does not.** It ships
  `SERVER_SETTINGS_MODE=manual`. The old claim would have made the dashboard's
  warning false for a default jammsen deployment.
- **Phase 9's #26 was mislabelled "solo-world export".** The roadmap meant a uid
  remap. True solo *extraction* would delete every other player's data,
  destroying the world it is meant to preserve.
- **Teleport: "will not build" → built, by a different route.** The RCON finding
  stands (see §4); the save-based coordinate teleport is a separate mechanism.

---

## 3. Known gaps

**Blocking nothing, but worth knowing:**

- **World Tree orientation is unverified.** Its extent is now exact, but no
  known pixel position on that landmass has ever been checked, so a flip would
  go unnoticed. Building anything up there settles it.
- **Non-Steam (Xbox/PS5/Mac) players are unverified** — task #33. The save
  carries `PlayerPlatform` and the parser surfaces it, but no console player has
  ever been observed. `docs/CROSSPLAY.md` records exactly what is known, what is
  not, and the three checks to run the day one joins.
- **Dungeon entrances are not extractable from the pak.** `BP_Dungeon*` yields
  2,091 objects but they are interior furniture, and interiors are separate
  sub-levels that land off-grid. Only ~15 entrances are placed on the overworld;
  the rest are runtime-spawned from `DungeonPointMarkerSaveData`.
- **Tower/field boss positions** are not extracted.
- **Player and technology imports are refused.** Container and Pal imports work.
- **Creating a Pal by import needs one of that species already in the world**, since
  `palclone` copies a record rather than inventing one. Refused with that reason,
  not guessed at.
- **`fieldBosses` and `areasFound` have no true denominator** — they fall back
  to the observed union across players and are labelled "discovered".
- **No 2FA or password-reset flow.**
- **S7 (CSRF tokens) and S11 (dependency scanning)** remain open in
  `docs/AUDIT.md` §5. Both are mitigated rather than closed.
- **Multi-image Docker validation was done from image metadata, not runs.**
  `docs/COMPATIBILITY.md` says so explicitly and gives the command to re-check.

---

## 4. Two things deliberately not built

**Game Pass save extraction — removed 2026-07-30**, at the operator's request.
It solved a Windows storage-location problem (finding the save inside
`WGS` container folders), not the console-player question it was assumed to
answer. What was actually wanted is task #33, above.

**Teleport over RCON — closed, will not build.** Verified against the shipped
`PalServer-Linux-Shipping` binary rather than community docs. The only
player-facing command is `TeleportToPlayerByIndex`; there is no coordinate form.
The decisive point is not the missing coordinates but that **both admin
teleports anchor to the issuing admin's in-game character**, and a headless
dashboard has none. Adding an RCON client would buy a command it structurally
cannot use.

The save-based coordinate teleport that *was* built (`teleport.py`) is a
different mechanism with a different limitation: it needs the server stopped, so
it cannot unstick a player who is online right now.

---

## 5. Open items

### Needs you
| # | Item |
|---|---|
| — | **World Tree**: build or open anything on that landmass to confirm map orientation |
| 33 | **Non-Steam players**: surfaces itself when a console player joins; `docs/CROSSPLAY.md` has the checks |

### Optional hardening, none gating a LAN deployment
| # | Item | Size |
|---|---|---|
| S7 | CSRF tokens (mitigated today by `SameSite=Lax` + POST-only mutations) | small |
| S11 | `npm audit` + `pip-audit` in CI | small |
| — | Multi-image Docker validation by actually running jammsen | medium |
| — | API versioning | small |

---

## 6. Verification for this snapshot

Run on 2026-07-30 against the current tree:

| Check | Result |
|---|---|
| `pytest` (backend, incl. integration against a real world) | **991 passed, 0 failed** · 20 m 38 s |
| `npm test` (vitest) | **82 passed** |
| `npx tsc --noEmit` | clean |
| `npm run lint` | **0 errors**, 20 warnings (all the pre-existing data-fetch-on-mount pattern) |
| `npm run build` | clean, **73 MB** output |
| `podman build` + run | **379 MB image**; container stays up as `uid=1000`, `/api/health` 200, backend refused from the host, sign-in 200 / bad password 401, proxy `POST` 405 |

**The suite now takes ~21 minutes, not the ~140 s the docs claimed** — 60
integration tests, each parsing a real 55 MB world, with the write paths taking a
full verified backup on top. `soloexport` is the single most expensive test
because it walks the entire node tree. Corrected in `README.md` and `AGENTS.md`.

### 6.1 Findings from this pass

**`next.config.ts` had no `outputFileTracingExcludes`.** `output: "standalone"`
copies traced files out of the project root, and the tracer was sweeping in
`refs/` (5.1 GB — the dedicated-server install, whose `PalWorldSettings.ini`
holds live server passwords) and `refworld/` (a real world save with real Steam
IDs and player names). Build output was **5.8 GB for a 73 MB app**.

`.gitignore` and `.dockerignore` both exclude those directories, so nothing ever
left the machine — but neither of them governs `.next/`, and the Dockerfile
copies `.next/standalone` wholesale out of the builder stage. Three ignore
mechanisms have to agree and only this one had no test.

Fixed, and pinned by `src/lib/build-config.test.ts`.

A side-finding while fixing it: **Turbopack's glob parser rejects character
classes** (`TurbopackInternalError: Parsing glob pattern`) and fails the build
outright, so `.gitignore`'s date-prefix pattern for the session transcripts
cannot be copied across verbatim.

Three smaller things closed in the same pass:

- **`main.py` used the deprecated `@app.on_event("startup")`**, which logged a
  warning on every container boot. Migrated to a lifespan handler and verified
  with `DeprecationWarning` promoted to an error.
- **Five unreferenced `create-next-app` SVGs** (`file`, `globe`, `next`,
  `vercel`, `window`) were shipping in every image. Removed.
- **`DISCOVERY_VISIBILITY` and `MAX_UPLOAD_MB`** are read by the code but were
  never mentioned in `.env.example`. Documented.
- **The README's "existing compose file" snippet put `BACKUP_DIR` under
  `/palworld`**, which is where the jammsen image keeps its *own* rotating
  snapshots, and which breaks if the mount is made read-only. Corrected to the
  named volume the main compose file already uses.
