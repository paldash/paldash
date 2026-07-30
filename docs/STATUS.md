# Status & Phase 8 preparation

Snapshot: **2026-07-29**. Phases 0–8 complete.

`789 backend + 77 frontend tests, 0 failures. 0 lint errors (17 warnings, all the
same data-fetch-on-mount pattern the codebase already uses).` Integration tests
run against a real 1.0 world and include every write path.

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

### Access control
Seven roles, two independent gates (role capability ∩ security level), a route
allowlist that is not a prefix match, scrypt password hashing, server-side
revocable sessions, per-IP and per-username throttling, and an audit record on
every mutating action.

### Reading the game's own files
The pak is unencrypted and Oodle-compressed, which this project already
decompresses. `scripts/palpak.py` extracts any of its 158,444 files;
`scripts/upackage.py` reads package export maps. Three results the save files
cannot give:

- the World Partition cell grid, which fixed the World Tree map
- all **396 effigies** with the GUIDs saves key on
- **35,687 world objects** — 24,359 ore nodes, 8,386 chests, 2,757 fishing spots,
  185 oil fields (`scripts/extract-world-objects.py`, 486 KB bundled)

Positions are static per game build; the save supplies the *state* (mined,
looted, respawning) and updates on a normal parse refresh.

### Privacy and visibility
Two independent dials, one server-wide and one per player:

- **`discoveryVisibility`** (Owner) — who sees map content nobody has found yet.
  `everyone`, any role name meaning that rank and above, or `nobody`.
- **`map_privacy`** (each player, about themselves) — `off`, `player`,
  `player_bases`, `guild`. **Defaults to the most private**, so nobody is exposed
  before they know the setting exists.

The privacy rule is one line: `hidden ⟺ viewer_rank <= hider_rank`. Privacy
applies to peers and below, never upward, so a player can never hide from staff
and no exemption list is needed. Applied server-side in both the save endpoints
and the REST proxy — live positions come from the game, not the save, so a
filter in only one place would leave a hidden player showing as a live dot.

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

`get_players()` is the one that mattered — four endpoints call it and the cost
is per player, so a 32-player server was paying ~73 ms of identical Oodle
decompression and GVAS parsing on every roster, progress and discovery request.

---

## 2. Numbers worth keeping

Every one of these is measured against real data, not quoted.

| Fact | Value | How it was established |
|---|---:|---|
| Streaming cell size | 25,600 | 174/174 fast-travel points land on an occupied cell |
| Effigies | 396 | Package export map; 37/37 collected ones verified against a save |
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

---

## 3. Known gaps

**Blocking nothing, but worth knowing:**

- **World Tree orientation is unverified.** Its extent is now exact, but no
  known pixel position on that landmass has ever been checked, so a flip would
  go unnoticed. Building anything up there settles it.
- **Dungeon entrances are not extractable from the pak.** `BP_Dungeon*` yields
  2,091 objects but they are interior furniture, and interiors are separate
  sub-levels that land off-grid. Only ~15 entrances are placed on the overworld;
  the rest are runtime-spawned from `DungeonPointMarkerSaveData`, which is
  exactly why the save has 170 and the pak does not.
- **Tower/field boss positions** are not extracted.
- **The 35,687 world objects are bundled but not on the map.** A layer that
  renders them needs viewport culling — that many markers at once is not
  reasonable to draw.
- **Player and technology imports are refused.** Container and Pal imports work.
- **Creating a Pal by import needs one of that species already in the world**, since
  `palclone` copies a record rather than inventing one. Refused with that reason,
  not guessed at.
- **`fieldBosses` and `areasFound` have no true denominator** — they fall back
  to the observed union across players and are labelled "discovered".
- **Migration tools** (Steam ↔ dedicated ↔ Game Pass) are not started.
- **No 2FA or password-reset flow.**
- **Multi-image Docker validation** has only been done against
  `thijsvanloef/palworld-server-docker`.

---

## 4. Phase 8 — server dashboard & admin commands · ✅ **COMPLETE**

Nothing in this phase writes to a save file, so for the first time since Phase 4
the corruption rule was not the dominant constraint. The dominant finding was a
different one.

### The finding: commands were reachable and unaudited
Kick, ban, announce, force-save and shutdown were **already reachable** through
the Next.js game-REST proxy, gated on `server.control` — and left **no audit
record**. The proxy has no `audit.record` call and cannot sensibly have one,
because SQLite is owned exclusively by the Python process.

So the work was not "build kick/ban", it was "move them somewhere they can be
recorded":

- `backend/gameapi.py` — the backend's own client for the game's REST API.
- `backend/moderate.py` — issues commands and audits them, including **failures**.
  An attempt that did not land still says who tried.
- The proxy now serves **reads only** and returns 405 with a message naming the
  right route. A 404 would read as "feature removed".

The target's display name is captured at the time of the action, because a uid is
unreadable and players rename themselves.

### The capability split
`server.control` bundled an operations decision (restart) with a social one (ban).
Now two: `server.control` and `players.moderate`. Moderator and above get both, so
**no existing account changed what it can do** — the point is that either can now
be withdrawn without the other.

### Metrics with history
`backend/metrics.py` + a `metrics` table. Sampled every 60s, kept 30 days, stored
**raw** — 43,000 rows, which SQLite answers instantly and which cannot disagree
with itself the way downsampled tables can. Bucketing happens at query time.

The design decision worth keeping: **a gap is data.** A sample is written even when
the game is unreachable, and `reachable` is averaged into a *fraction* per bucket
rather than a flag — so a bucket at 0.5 is an intermittently crashing server,
which is exactly what an operator is hunting and what a boolean would erase.
`players` is never coerced to 0 when the server is down.

### Load-aware throttling
`savecache.load_verdict`. **Fails open**, unlike everything else in this project,
and the asymmetry is deliberate: writing to a live save destroys a world, so
`safety.py` fails closed; refusing to parse forever over a missing signal merely
breaks the dashboard. No data, a stale sample, an unreachable server and a missing
table all read as "fine to parse".

It gates the *start* of a parse and never interrupts one in flight, and it runs
before any filesystem access — a test pins that ordering, because the first
version had the comment and not the behaviour.

An explicit Refresh gets a lower floor (12 fps vs 20): the operator asked and is
watching.

### Decisions taken
- **Retention:** 60s sampling, 30 days raw, no rollup. Configurable.
- **Capability:** split, as above.
- **Throttling:** gates the start; never kills a running parse.

---

## 5. Task list

### Done this cycle
- Phase 7 in full: slots, bulk, repair, skills, cloning, plus UI for all
- Pak extraction toolchain (`palpak.py`, `upackage.py`, `extract-effigies.py`)
- 396 effigies bundled with GUIDs; `reference_totals` corrected
- World Tree map extent derived from the streaming grid
- Discovery visibility policy + `/api/world/discoveries`, wired into the map
- Per-player map privacy (`privacy.py`), private by default, applied in both the
  backend and the REST proxy
- General world-object extractor with `--targets`
- Password masking in the settings API and audit log
- Env-managed settings warning
- GPL-3.0 `LICENSE` + `docs/LICENSING.md`
- Container Stop/Start buttons hidden unless configured
- README, AGENTS.md, FEATURES.md brought current

- Request-path caching (`viewcache.py`) and the player-save path index
- Pal import (#27) — `pal` export kind, `palimport.py`, UI, 33 tests
- `docs/DEPLOYMENT.md` (#29), including working stop/start commands
- Password rotation closed out (#22) — see below
- **Phase 8 in full**: `gameapi.py`, `moderate.py`, `metrics.py`, load-aware
  throttling, the `players.moderate` capability split, the metrics-history and
  moderation UI, and closing the unaudited POST path through the game-REST proxy

### Open — needs you
| # | Item |
|---|---|
| — | World Tree: build or open anything on that landmass to confirm map orientation |

#22 is **done**. The code side was already complete — `settings_ini.SECRET_KEYS`
masks `AdminPassword` and `ServerPassword` on every read and in the audit log,
and `read_ini(reveal=True)` is used only by the write path. Verified that no
`PalWorldSettings.ini` or `.env` has ever been committed (`git log --all` over
those paths is empty) and that session transcripts are gitignored twice over.
The only outstanding action was rotation, which is done.

### Open — buildable now
| # | Item | Size |
|---|---|---|
| — | ~~Teleport~~ — **closed, will not build.** See below. | — |
| — | Game Pass save extraction (`xgp_save_extract.py`); no Game Pass save to verify against | medium |
| — | Remaining server presets — check against `DefaultPalWorldSettings.ini`'s 119 keys, not memory | small |
| — | Multi-image Docker validation (needs real container runs) | medium |
| 26 | Phase 9 remainder: Game Pass extraction, server presets, multi-image Docker validation | 2–3 days |

### Teleport — closed, will not build (2026-07-30)

Verified against the shipped `PalServer-Linux-Shipping` binary rather than against
memory or community docs.

- The only player-facing teleport command is **`TeleportToPlayerByIndex`**. There is
  **no coordinate teleport** in the server's command surface at all.
- The `Debug_TeleportToBotLocation`, `Debug_TeleportToNearestCamp`,
  `Dev_RequestTeleportToBossTower_ToServer` and
  `Dev_TeleportToRelativeLocationInStageLevel_ToServer` symbols are development
  RPCs, not admin commands, and are not reachable over RCON.
- The decisive point is not the missing coordinates: both admin teleports are
  **anchored to the issuing admin's in-game character** ("teleport me to X",
  "teleport X to me"). A headless dashboard has no character in the world, so there
  is no anchor. Adding an RCON client would buy a command it structurally cannot use.

**The one alternative, deliberately not built:** a player's position lives in their
character record, so a coordinate teleport *is* achievable as a save edit, and
`charedit` already has the write path, validation and rollback. It would only work
with the server stopped — which is useless for the case that motivates teleport in
the first place, unsticking a player who is online right now.
