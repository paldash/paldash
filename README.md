# Palworld Server Dashboard

A web dashboard for a self-hosted Palworld dedicated server. It combines the
server's REST API (live status, players, admin actions) with direct reads of the
save files (bases, guilds, Pals, inventories, breeding) and an editor for
`PalWorldSettings.ini`.

Designed to run in a container next to your server, sharing the same bind mount,
and to stay out of the game server's way.

---

## Palworld 1.0 support (read this first)

Palworld 1.0 (the Summer 2026 update) changed the save format. Saves are now
compressed with **Oodle Kraken** and the magic bytes changed from `PlZ` to `PlM`.

**The `palworld-save-tools` PyPI package cannot read 1.0 saves.** On a real 1.0
`Level.sav` it fails immediately:

```
Exception: not a compressed Palworld save, found b'PlM' instead of b'PlZ'
```

Several of its `RawData` decoders are also out of date for 1.0 (character data
raises `EOF not reached`). This project therefore uses **`palsav` + `palooz`**
from the actively maintained [PalworldSaveTools][pst] project instead. `palooz`
is a C++ extension around the open-source `ooz` Kraken decoder and lives in a git
submodule, so it must be built from a checkout **with submodules** — a plain
`pip install git+…` will not fetch it. The Dockerfile handles this for you.

Verified against a real 1.0 save: a 2.0 MB `Level.sav` decompresses to 55.6 MB in
0.05 s and parses in ~3 s.

---

## Quick start

```bash
cp .env.example .env
# set PANEL_PASSWORD and PALWORLD_ADMIN_PASSWORD, then:
docker compose up -d --build
```

Dashboard on `http://localhost:3000`.

The bundled `docker-compose.yml` runs both the game server and the dashboard on a
private bridge network sharing `./palworld`. If you already run a server, don't
use that file — use the snippet below.

---

## Adding this to an existing compose file

Copy this one service into your existing compose file. Three things must line
up: the **same bind mount**, a **shared network**, and `RESTAPIEnabled=True` on
the server.

```yaml
services:
  # ... your existing palworld server service ...

  dashboard:
    build: ./palworld-dashboard    # or: image: palworld-dashboard:latest
    container_name: palworld-dashboard
    restart: unless-stopped
    depends_on:
      - palworld                   # <- your server's service name
    ports:
      - "3000:3000"
    volumes:
      - ./palworld:/palworld       # <- EXACTLY the same mount your server uses
      - dashboard-cache:/app/cache   # parse cache AND the accounts database
    environment:
      # http://<your server's service name>:<REST port>
      - PALWORLD_REST_URL=http://palworld:8212
      - PALWORLD_ADMIN_PASSWORD=${PALWORLD_ADMIN_PASSWORD}
      - PANEL_PASSWORD=${PANEL_PASSWORD}   # creates the first Owner account
      - SAVE_BASE_DIR=/palworld/Pal/Saved/SaveGames/0
      - BACKUP_DIR=/palworld/backups
      - CACHE_DIR=/app/cache
    networks:
      - palnet                     # <- the same network as your server
    mem_limit: 3g

volumes:
  dashboard-cache:

networks:
  palnet:
    driver: bridge
```

Checklist:

1. **Same host path** on both services. If your server mounts
   `/opt/palworld:/palworld`, the dashboard must mount `/opt/palworld:/palworld`
   too.
2. **Same network.** If your server has no `networks:` key, add `palnet` to both.
   `PALWORLD_REST_URL` uses Docker's DNS, so no `host.docker.internal` and no
   published REST port.
3. **`RESTAPIEnabled=True`** and `AdminPassword` set on the server. Most images
   expose these as `REST_API_ENABLED` / `ADMIN_PASSWORD`.
4. **Do not publish port 8212.** Palworld's own docs warn against exposing the
   REST API; the dashboard reaches it over the private network.
5. If `SAVE_BASE_DIR` doesn't exist, check your image's layout — some use
   `/palworld/Pal/Saved/...`, others differ. The Settings tab reports the path it
   resolved.

---

## Not corrupting your save

This was the primary design constraint. The guarantees:

**Reads never touch the file.** Save files are opened `O_RDONLY`, never locked
and never moved. Because a live server rewrites `Level.sav` during autosaves, a
naive read can return a torn buffer — so every read snapshots `(size, mtime)`,
reads, then re-checks. If the file moved underneath us, it backs off and retries
instead of handing a truncated buffer to the parser.

**Writes are fail-closed.** The old logic treated "REST API unreachable" as
"server offline", which meant a wrong admin password, a typo'd URL or a DNS
hiccup would have unlocked the save editor **on a live server**. It now decides
from four signals — REST probe, TCP port, recent `.sav` mtime, and process scan —
and a write is permitted only when the server is *positively proven* stopped. Any
"running" vote, or an inconclusive result, locks writing. The sidebar shows the
verdict and the Settings tab shows each signal's reasoning.

**Every mutation is backed up first.** All save-directory changes go through a
guard that re-checks server state, takes a full world backup, then re-checks
again immediately before applying. If the backup fails, the change is abandoned.
`PalWorldSettings.ini` is copied to `BACKUP_DIR/config/` before any write, and is
written atomically (temp file → `fsync` → `rename`), so an interrupted write
leaves the original intact. Restores snapshot the current world first, so a
restore is itself reversible.

**Belt and braces.** Set `SAVE_READ_ONLY=true` to refuse all writes regardless of
server state, and/or mount the volume `:ro`. Leave `ALLOW_UNVERIFIED_EDITS=false`
— setting it true lets an *inconclusive* check count as stopped, which is exactly
how a live world gets corrupted.

The save round-trip has been verified byte-for-byte on a real 1.0 save:
decompress → recompress reproduces the original file exactly, and
`GvasFile.read` → `.write` reproduces all 55,668,985 bytes identically.

---

## Stopping and restarting the server

**Palworld's REST API can stop the server. It cannot start one.** `/shutdown` and
`/stop` kill the game process and know nothing about containers, so what happens
next depends entirely on how your server is supervised:

| Your setup | What "shutdown" does |
|---|---|
| PalServer is the container's main process | It exits → container exits → `restart: unless-stopped` starts it again. Behaves like a restart. |
| A wrapper/supervisor keeps running | Container stays up with **no game server inside it**. The server is down until you intervene. |

The second case is common, so the dashboard never claims it restarted anything.
After a shutdown it watches for the server to return and, if it doesn't, says so
plainly:

> **The server has not come back.** A shutdown was issued 190s ago and the game
> process is still gone… Restart the server container to bring it back.

The button is labelled **"Announce & stop"** unless a real restart mechanism is
configured, in which case it becomes "Announce & restart".

### Giving it a real restart button

Optional, and off by default because it requires container control:

```yaml
- RESTART_COMMAND=docker -H tcp://docker-proxy:2375 restart palworld-server
```

Mounting `/var/run/docker.sock` into the dashboard is **root-equivalent on the
host** — a bad trade for a container with a web login. Use the commented
`docker-proxy` service in `docker-compose.yml` instead: it exposes only
container inspection and restart, with images, volumes, networks and exec all
denied. The command is read from the environment and executed via `shlex`
without a shell, so nothing a user types can be injected into it.

If you'd rather not, just restart manually — it's one command:

```bash
docker compose restart palworld
```

### Maintenance mode

Save writes are only permitted while the server is provably stopped, and the
clean way to get there is to stop the **container**, not just the game process —
a stopped container cannot relaunch the server underneath an in-progress write.

The Save Tools tab has a Maintenance panel that runs the whole cycle:

1. Stop the server container (button, or it shows you the command)
2. Wait until all four safety signals agree the server is down — editing unlocks
   itself at that point
3. Make your changes; each one takes its own backup
4. Start the container again

Both directions work by hand with no privileges at all, because the dashboard is
a *separate container* — stopping the `palworld` service does not touch it:

```bash
docker compose stop palworld     # dashboard stays up, unlocks editing
docker compose start palworld    # bring it back
```

Configure `STOP_COMMAND` / `START_COMMAND` to get buttons for the same thing.

---

## Keeping it light

Measured on a real 2.0 MB / 55.6 MB-decompressed `Level.sav`:

| Stage | Time | Peak RSS |
|---|---|---|
| Oodle decompress | 0.05 s | — |
| Full parse + extract | ~3.2 s | ~445 MB |
| Same, without item containers | ~3.1 s | ~439 MB |

Peak memory runs roughly **8× the decompressed size**, so a much larger world
scales accordingly — budget for it with `mem_limit`. Item decoding costs only
~2%, so leave `PARSE_INCLUDE_ITEMS=true` unless memory is very tight; **parse
frequency is the real lever.**

How it stays cheap:

- **Nothing parses on a timer.** By default (`PARSE_AUTO=false`) the save is read
  only when you press **Refresh**. Opening tabs, polling, and every read endpoint
  serve cached data and never trigger work — so between refreshes the dashboard
  costs your server nothing. Set `PARSE_AUTO=true` to let stale data refresh
  itself, rate-limited by `PARSE_MIN_INTERVAL_SECONDS`.
- Parsing runs in a **separate niced subprocess** (`nice 19`, idle I/O priority)
  so it yields to the game server, and its memory is returned to the OS on exit.
- One parse at a time, hard timeout, results cached to disk so a container
  restart doesn't re-parse.
- **Only the decoders in use are registered.** Foliage grids, map objects, work
  data and dynamic items stay as opaque bytes rather than being decoded into
  millions of Python objects.
- The UI polls live REST metrics every 5 s (cheap, it's just the game's API) and
  save-derived data every 2 min.

If you want it even lighter, set `PARSE_ENABLED=false`. Everything driven by the
REST API — status, players, FPS, admin actions, settings, broadcasts — keeps
working; only save-derived views (bases, guilds, breeding) go dark.

---

## Access control

**Accounts.** Every person gets their own login. On first start with no accounts,
`PANEL_PASSWORD` creates the first **Owner** automatically, so an existing
deployment keeps working with the same password — add real accounts from the
**Users** tab afterwards.

Seven role presets, least to most privileged:

| Role | Can do |
|---|---|
| **Guest** | Not signed in. Only what the visibility toggles allow, with names and IDs stripped. |
| **Read only** | A named account that can look but not touch. |
| **Player** | Server overview plus their own character. |
| **Trusted player** | Full visibility of other players, guild inventories, breeding planner. Still read-only. |
| **Moderator** | Kick, ban, announce, restart, take backups, read the audit log. |
| **Administrator** | Server settings and save editing as well. |
| **Owner** | Everything, including accounts and the security policy. |

**Two gates, both must agree.** A role grants a capability; the security level
withholds it. An Owner on a `readonly` server still cannot write — that dial is
about protecting the world from mistakes, not about trust. Nobody can grant a
role above their own, and the last Owner cannot be demoted, disabled or deleted.

**Sessions are revocable.** Signing out, disabling an account, changing someone's
role or changing a password all take effect immediately rather than whenever a
cookie happens to expire. Tokens are stored hashed, so a stolen database does not
hand over live sessions.

**Sign-in is throttled** per address and per username, with exponential backoff
that survives a restart.

**Everything is audited.** Every save write, settings change, restore, container
stop, account change and policy change is recorded with who, when, from where and
whether it succeeded — including refusals. The log is append-only: there is no
endpoint that deletes an entry, and entries age out on a retention timer whose
own runs are logged.

```yaml
# Which parts of the world guests may see, and the write ceiling.
- SECURITY_LEVEL=safe          # readonly | safe | full — a ceiling the UI cannot raise
- GUEST_VIEW_ENABLED=true
- GUEST_SEE_CHESTS=false
- AUDIT_RETENTION_DAYS=180
- MIN_PASSWORD_LENGTH=10
- SESSION_TTL_HOURS=12
```

## Server settings & PvP

The Settings tab edits `PalWorldSettings.ini` directly, with presets. The one you
asked about — **player damage on, bases protected**:

```ini
bIsPvP=True
bEnablePlayerToPlayerDamage=True
bEnableDefenseOtherGuildPlayer=False
bCanPickupOtherGuildDeathPenaltyDrop=False
bEnableFriendlyFire=False
```

`bIsPvP` alone does not enable player damage; `bEnablePlayerToPlayerDamage` does.
`bEnableDefenseOtherGuildPlayer` is the lever that governs rival guilds
interacting with your base.

**Nothing in this file is hot-swappable.** The server reads it only at boot, and
the REST API has no settings-write endpoint, so there is no way to apply an INI
change to a running server. Writes land immediately and safely (it's the config
directory, not the save directory) but take effect on restart. The tab has an
"announce & restart" action that broadcasts an in-game countdown, then asks the
server to shut down — pair it with `restart: unless-stopped` so it comes back.

Unknown keys are rejected rather than appended: a typo'd key silently added to
`OptionSettings` is how you end up with a server that won't boot. If your server
image regenerates the INI from environment variables at startup, edit those
instead or your changes will be overwritten on the next restart.

---

## Breeding

Uses the game's own full pair table — all 299 Pals and 44,850 parent
combinations — rather than a reimplementation of the CombiRank formula, so
special pairings are correct by construction (Sparkit × Relaxaurus →
Relaxaurus Lux verifies correctly).

It's driven by the Pals actually in the save: pick a player, see their species
and gender counts, every child reachable in one step (gender-aware — a pair needs
a male and a female), and a route finder from what they own to a target Pal.

---

## Configuration

See `.env.example`; every variable is documented there.

---

## Credits

This project stands on other people's work:

- **[PalworldSaveTools][pst]** by deafdudecomputers, used two ways under two
  different licences:
  - The `palsav` / `palooz` packages that read Palworld 1.0's Oodle-compressed
    `PlM` saves. Licensed **GPL-3.0-or-later** and used here as a library; if you
    redistribute this project, that licence applies to your distribution.
  - Its `resources/game_data/` tables (**MIT**, © 2026 Pylar), which this project
    compiles into `backend/data/gamedata.json.gz` — every item, Pal, passive,
    active skill, technology and structure name, plus all 174 fast-travel points
    with world coordinates. Regenerate with `scripts/build-gamedata.py`.
- **[ooz](https://github.com/powzix/ooz)** by powzix — the open-source Kraken
  decompressor that `palooz` wraps.
- **[palcalc](https://github.com/tylercamp/palcalc)** by tylercamp (**MIT**) —
  the breeding pair table in `backend/data/`, extracted from the game's own
  CombiRank tables, plus the coordinate reference samples used to calibrate
  world → map conversion.
- **[palworld-save-tools](https://github.com/cheahjs/palworld-save-tools)** by
  cheahjs — the original GVAS reader this ecosystem is built on, and the
  reference for the save structure.
- **[RNZ01/palworld-server-dashboard](https://github.com/RNZ01/palworld-server-dashboard)**
  — the dashboard that inspired this one.
- Palworld is © Pocketpair, Inc. This project is unofficial and unaffiliated.

---

## Map & items

**Two maps, not one.** Palworld 1.0 has the Palpagos Islands and the World Tree,
and they are separate maps in-game with separate framings — verified by checking
all 174 fast-travel points against the coordinate transform: 157/157 Palpagos
points land on the Palpagos image, 0/17 World Tree points do. Use the region
switcher above the map. Both images install with:

```bash
python3 scripts/install-map-assets.py    # extracts from refs/ into public/maps/
```

Positions on **Palpagos are calibrated and verified**. Positions on the **World
Tree are approximate** and the UI says so: no save has any objects on that
landmass yet, so there is nothing to fit the transform against. It becomes exact
as soon as anyone builds a base or opens a chest there.

**Map.** Points of interest come from the save itself — no external dataset
needed. On a real world that's ~4,100 markers: chests (with opened/unopened
state), ore and mining nodes, oil-rig crates, palboxes, breeding farms, statues,
crafting benches, production nodes and
storage, plus guild bases drawn with their actual build radius and live player
positions. Layers toggle individually and chests default to off because a mature
world has thousands.

Coordinates match the in-game map, using a transform fitted to reference samples
(±0.5 map units). Drop a 4096×4096 Palworld map image at
`public/palworld-map.png` to replace the plain grid background — the previous
build drew a *procedurally generated fake island*, which made markers look like
they were floating in the sea.

**Items.** The Items tab totals every item across every container in the world —
base chests, guild chests, player inventories, palboxes. On the test world that's
645 item types and 8.3 million items across 11,639 containers. Totals are
computed during the parse, so opening the tab costs nothing.

---

## Save editing

Container sorting is implemented and verified. Everything else is not — see the
gaps below.

**Permissions are capabilities, not roles.** `save.sort.stackables`,
`save.sort.all` and `save.edit.full` are separate grants, enforced in the API
proxy. Today they all map to the admin role; introducing per-user permissions
later means changing the capability map in `src/lib/permissions.ts` and nothing
else.

**Every write follows the same pipeline:**

```
assert provably stopped -> full backup -> mutate in memory
-> conservation check -> atomic write -> RE-READ FROM DISK
-> conservation check again -> automatic rollback on any mismatch
```

The invariant is conservation: sorting may reorder and merge stacks, but the
total quantity of every item in every container must be identical afterwards. If
one count is off, the write is rejected and the backup restored. A sort that
loses items fails loudly rather than quietly eating someone's ore.

Two deliberate conservatisms: empty slots are never fabricated (clearing a slot
reuses the byte representation of an empty slot already in that container), and
merging never builds a stack larger than one the save already contains, since
real per-item stack limits are not stored in the save.

**Measured on a real 1.0 world** (11,639 containers, 8,349,417 items):

| Mode | Containers | Slots | Result |
|---|---|---|---|
| `stackables` | 2,164 | 6,258 | totals identical, 1,516 durability items untouched |
| `all` | 2,177 | 6,419 | totals identical, all 1,516 dynamic IDs preserved |

Independently re-parsed and compared against the original both times: 645 item
types, per-item totals identical, zero mismatches.

---

## Development & tests

```bash
./scripts/setup-dev.sh          # builds .venv, compiles palsav from refs/
.venv/bin/python -m pytest      # backend: 293 tests, ~140s
npm test                        # frontend: 34 tests, <1s
```

The suite is in tiers:

| Command | Tests | Time | Needs |
|---|---:|---:|---|
| `npm test` | 34 | <1s | nothing |
| `pytest -m "not integration"` | 275 | ~35s | nothing |
| `pytest` | 293 | ~140s | `refworld/` + `palsav` |

Unit tests cover the corruption guard (every way it must refuse to write), path
handling, the settings-INI parser, the access-policy ceiling, the container sort
algorithm on synthetic data, password hashing, session revocation, login
throttling, and the role model. The frontend tests cover the proxy route
allowlist. Integration tests run the real pipeline against
a real world: parse a 55 MB save, sort every container, write it, re-read from
disk and prove not one item moved in or out. They skip cleanly when `refworld/`
is absent, so a fresh checkout still runs green.

If you change anything under `backend/safety.py`, `backend/backup.py` or
`backend/saveedit.py`, run the full suite — the slow tests are the ones that
actually prove the save is safe.

---

## Known gaps

Being straight about what is not done:

- **The general save editor** (`/api/edit`) still returns 501 — player stats,
  Pal IVs and individual slot edits are not exposed. The write path is proven
  (sorting uses it), but a general editor has far more ways to produce a world
  the game refuses to load, so each field needs validating first.
- **Tower bosses, dungeons and effigies** are not on the map. Fast-travel
  statues now are — all 174, from bundled game data — but those three have no
  published coordinates in any dataset we have. Dungeons are *partially*
  available: the save records 170 markers and their state, but by spawn-area
  name, not position.
- **The World Tree map transform is provisional.** See "Map & items" above.
- **Per-player logins** are not built. The data exists —
  `UnlockedWorldMapFlags` (fog of war), `FastTravelPointUnlockFlag`,
  `PaldeckUnlockFlag`, boss-defeat flags — so per-player fog-of-war views are
  feasible.
- **Per-base item breakdown**: items are totalled server-wide. Objects carry
  `base_camp_id_belong_to`, so grouping chest contents per base is a small step
  from here.

[pst]: https://github.com/deafdudecomputers/PalworldSaveTools
