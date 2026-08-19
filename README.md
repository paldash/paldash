# paldash

*A self-hosted dashboard for Palworld dedicated servers.*

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

**`docs/DEPLOYMENT.md`** goes deeper: which server images are actually tested,
matching PUID/PGID on the shared mount, read-only mode, working stop/start
commands, publishing an image instead of building from a clone, and the three
container traps that only show up on a real build.

---

## Documentation

| File | What it answers |
|---|---|
| `docs/DEPLOYMENT.md` | How to run it, and how to ship it as an image |
| `docs/CONFIGURATION.md` | Every environment variable, every option, and what each means |
| `docs/ROLES.md` | Who can do what: roles, capabilities, visibility settings, privacy |
| `docs/ARCHITECTURE.md` | How the codebase is put together, and why |
| `docs/TESTING.md` | How to run and write tests, and the traps that have fired |
| `docs/FEATURES.md` | What exists today — read before concluding something is unbuilt |
| `docs/STATUS.md` | Current state, measured numbers, open items |
| `docs/AUDIT.md` | The gap analysis and phased roadmap |
| `docs/COMPATIBILITY.md` | Which server images work, and where they differ |
| `docs/UPGRADING.md` | What to do when Palworld or the dashboard updates — operator and maintainer |
| `docs/CROSSPLAY.md` | What is known about non-Steam players, and what is not |
| `docs/LICENSING.md` | Why this is GPL-3.0, and what that does and does not require |
| `docs/GAMEDATA-SOURCES.md` | Where every fact about the game comes from — read before designing |
| `docs/DATATABLES.md` | Machine-generated index of all 471 server-pak DataTables |
| `AGENTS.md` | The subtleties that have already caused bugs |

---

## Adding this to an existing compose file

Copy this one service into your existing compose file. Three things must line
up: the **same bind mount**, a **shared network**, and `RESTAPIEnabled=True` on
the server.

Put this repository in a directory **beside your compose file**, because
`build:` is resolved relative to the compose file, not to your shell's working
directory:

```
your-server-dir/
├── docker-compose.yml
├── .env
├── palworld/               <- the game's data, already bind-mounted
└── paldash/                <- this repo
```

```yaml
services:
  # ... your existing palworld server service ...

  dashboard:
    build: ./paldash               # or: image: ghcr.io/paldash/paldash:1.0
    container_name: paldash
    restart: unless-stopped
    depends_on:
      - palworld                   # <- your server's service name
    ports:
      - "3000:3000"
    volumes:
      - ./palworld:/palworld       # <- EXACTLY the same mount your server uses
      - dashboard-cache:/app/cache     # parse cache AND the accounts database
      - dashboard-backups:/app/backups # keep these OFF the server's directory
    environment:
      # http://<your server's service name>:<REST port>
      - PALWORLD_REST_URL=http://palworld:8212
      - PALWORLD_ADMIN_PASSWORD=${PALWORLD_ADMIN_PASSWORD}
      - PANEL_PASSWORD=${PANEL_PASSWORD}   # creates the first Owner account
      - SAVE_BASE_DIR=/palworld/Pal/Saved/SaveGames/0
      - BACKUP_DIR=/app/backups
      - CACHE_DIR=/app/cache
    mem_limit: 3g

volumes:
  dashboard-cache:
  dashboard-backups:
```

Checklist:

1. **Same host path** on both services. If your server mounts
   `/opt/palworld:/palworld`, the dashboard must mount `/opt/palworld:/palworld`
   too.
2. **No `networks:` needed if both services are in the same compose file.**
   Compose puts every service in a project on one default network and gives each
   a DNS name, so `http://palworld:8212` resolves already. You only need an
   explicit shared network when the two live in *separate* compose files.
3. **`RESTAPIEnabled=True`** and `AdminPassword` set on the server. Most images
   expose these as `REST_API_ENABLED` / `ADMIN_PASSWORD` (jammsen spells the port
   `RESTAPI_PORT`, thijsvanloef `REST_API_PORT`).
4. **Do not publish port 8212.** Palworld's own docs warn against exposing the
   REST API; the dashboard reaches it over the private network.
5. **Keep `BACKUP_DIR` on its own volume**, not under `/palworld`. Two reasons:
   mounting `/palworld:ro` would otherwise break backups, and `/palworld/backups`
   is where the jammsen image keeps *its* rotating snapshots — two backup systems
   in one directory is a bad trade.
6. **`PUID`/`PGID` must match.** The dashboard image is built for uid/gid 1000,
   which is both server images' default. If yours differ, pass
   `args: { APP_UID: "…", APP_GID: "…" }` under `build:` rather than reverting to
   root.
7. If `SAVE_BASE_DIR` doesn't exist, check your image's layout. The Settings tab
   reports the path it resolved.

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
leaves the original intact.

**Backups are verified archives, not directory copies.** Each one is a single
`.tar.gz` with a manifest recording a SHA-256 for every file and one for the
archive itself, so "a backup exists" and "a backup will restore" are the same
statement. Verifying re-hashes the lot. A restore verifies *before* touching
anything — restoring a corrupt backup over a working world would be the worst
possible outcome — and leaves its own rollback point, so a restore is reversible.

Restores are two steps: **preview, then confirm**. The preview hashes rather than
compares sizes, and lists files that exist now but are absent from the backup
(players who joined since) as *kept* — restoring does not delete them. You can
restore the whole world, just `Players/`, or just the config.

Retention thins rather than truncates: newest few, then one per day, then one per
week. Rollback points taken automatically before an edit are protected while
they are still fresh, because they are the only way back from a bad edit.

Scheduled backups are off by default; when enabled, a missed window is skipped
rather than replayed, so a machine that was asleep will not wake up and take a
week of catch-up backups.

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
- RESTART_COMMAND=node -e "fetch('http://docker-proxy:2375/containers/palworld-server/restart',{method:'POST'}).then(r=>process.exit(r.status<300||r.status===304?0:1)).catch(()=>process.exit(1))"
```

That calls the Docker HTTP API with `node`, **not** the `docker` CLI — which is
deliberately not installed in the runtime image, so a command beginning with
`docker` fails with "not found". Node ships a global fetch and the image is
node:20, so this needs nothing extra. `docs/DEPLOYMENT.md` §4 covers stop/start
as well, and why `304` counts as success.

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

The two container buttons appear **only if you configure `STOP_COMMAND` /
`START_COMMAND`**. They are off by default and the runtime image deliberately
does not ship a `docker` binary, so showing them unconditionally would mean two
buttons that always fail. You lose nothing by leaving them off — the commands
above do exactly the same thing, and the dashboard notices the server going down
on its own either way.

If you do want them, point them at a **socket proxy** rather than mounting
`/var/run/docker.sock`; access to the raw socket is equivalent to root on the
host. See the commented-out block in `docker-compose.yml`.

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

**Moderating players and controlling the server are separate capabilities**
(`players.moderate` and `server.control`). Both go to Moderator and above by
default, but banning a griefer and shutting the world down are different kinds of
trust, so either can be withdrawn without the other.

**Every command is audited, including the ones that fail.** Kick, ban, unban,
announce, force-save and shutdown all go through the backend rather than straight
to the game, precisely so there is a record: who did it, to whom, why, and whether
it worked. The target's name is captured at the time — a Steam ID is unreadable six
months later.

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

---

## Player privacy

Separate from access control, and pointing the other way: access control is what
staff may see, privacy is what a player may hide.

**Each player chooses, about themselves**, on the My account tab — four modes,
because bases belong to *guilds* and so "hide me" has more than one honest
meaning:

| Mode | Hides |
|---|---|
| `off` | Nothing |
| `player` | Their live position and roster entry |
| `player_bases` | That, plus their bases — solo guilds only |
| `guild` | The whole guild's bases. The one mode with a social cost |

**The whole rule is one comparison:** `hidden ⟺ viewer_rank <= hider_rank`.

- A player can **never hide from staff**, so moderation works without anyone
  maintaining an exemption list.
- **Equal rank is concealed.** Peers are exactly who a privacy setting is for.

**The default is the most private mode**, not the least. Nobody should have to
discover a privacy setting exists before they stop being exposed, and it costs
little because staff see everyone regardless.

**Bases have their own switch**, held by the guild master (with a fallback if the
master has no dashboard account, so a guild is never locked out). Staff get no
override on that one.

Filtering happens **server-side, in two places** — the save endpoints and the
live REST proxy. A filter in only one leaves a hidden player missing from the map
and still showing as a live dot on the same screen.

Privacy governs map and roster visibility only. The audit log, account management
and save editing all work on real identities regardless.

---

## Server operations

**Metrics with history.** CPU, memory and disk sampled every 60 s and kept 30
days raw. Under Docker the CPU and memory figures come from cgroup files, so they
describe *this container's* limits rather than the machine's; disk is the
filesystem holding your save directory, the one whose filling up stops the game
saving.

**A gap is data.** A sample is written even when the game is unreachable, so a
chart cannot interpolate a smooth line straight through an outage. `reachable` is
averaged into a fraction per bucket rather than a flag — a bucket at 0.5 is an
intermittently crashing server, which is exactly what you would be hunting and
what a boolean would round away.

**Scheduled announcements.** Recurring messages on intervals from 15 minutes to
daily. An empty server *consumes* its window rather than queueing it, so the
first person to log in is not met with every overdue message at once.

**Update detection.** The dashboard reads the server install's Steam
`appmanifest` for its build id — two file reads, no network — and banners when
the game has moved past the build the bundled data came from. If the install
directory is not mounted (the normal setup mounts only the save path) it says
**"cannot tell"** rather than a reassuring "up to date".

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
`OptionSettings` is how you end up with a server that won't boot.

**Your container probably owns some of these settings, not this file.** Both
`thijsvanloef/palworld-server-docker` and `jammsen/docker-palworld-dedicated-server`
rewrite `PalWorldSettings.ini` from environment variables *on every start*, so a
change saved here survives until the next restart and is then silently reverted —
worse than a refusal, because you watched it succeed. The settings tab now flags
those keys with the variable that overrides them (`SERVER_NAME`,
`ADMIN_PASSWORD`, `PLAYERS`, `RCON_*`, `REST_API_*`, and others) and points at
your `.env`. It cannot *detect* which ones your setup actually sets — one
container cannot read another's environment — so treat it as a warning, not a
verdict.

**Passwords are never returned by the API.** `AdminPassword` and `ServerPassword`
read back as empty with a "set / not set" marker, and a password change is
recorded in the audit log as `(hidden)` rather than storing the old and new
values permanently. Submit an empty value to leave one unchanged. To rotate the
admin password on a containerised server, change it in `.env` and recreate the
container — not here.

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
  - Its `resources/game_data/` tables (**MIT**, © 2026 Pylar), which supply
    **the icon paths and which ids exist at all** — plus the 174 fast-travel
    points with world coordinates, and the stat formula in its
    `.opencode/skills/pst-stat-formula/` that `backend/palstats.py`
    transcribes. Regenerate with `scripts/build-gamedata.py`.

    **This credit used to say "every item, Pal, passive, active skill,
    technology and structure name", and that is no longer what the archive
    supplies.** Display names and descriptions now come from the game's own
    `L10N/` tables via `scripts/l10n.py` and `scripts/gametext.py`, and the
    numeric columns from the server pak. Overcrediting is not a harmless
    courtesy — it misdescribes which licence covers what, and it points the
    next reader at the wrong source when a name is wrong.
    `backend/data/provenance.json` is the precise, per-artifact answer.
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
- **[Rock Paper Shotgun][rps]** — the element effectiveness chart, which is in
  neither the game pak nor any data archive. Cited because it is the one piece of
  game data here that was hand-entered rather than extracted; see "Element
  matchups". Damage multipliers are *not* taken from it, since it presents those
  as an image.
- **[Palworld Wiki](https://palworld.fandom.com)** — used twice, both documented
  where they land: the seven map-marker icons in `public/icons/map/` (see the
  `PROVENANCE.md` beside them — the game's own compass HUD art, which a headless
  server install does not ship pixels for), and the condenser progression table
  that `backend/condenser.py` tests against the game's own files.
- Palworld is © Pocketpair, Inc. This project is unofficial and unaffiliated.
  **Nothing here is credited that the built image does not contain** —
  `docs/LICENSING.md` holds the shipped-dependency table, and the two credits
  above that are lineage rather than dependency say so there.

[rps]: https://www.rockpapershotgun.com/palworld-element-chart

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

**What is on it comes from two sources.** The save supplies what players have
*done*; the game's own files supply what *exists*.

From the save (~4,100 markers on a real world): chests with opened/unopened
state, ore nodes, oil-rig crates, palboxes, breeding farms, statues, crafting
benches, production nodes and storage, plus guild bases drawn with their actual
build radius, and live player positions polled from the REST API.

From the game pak, bundled so nothing is fetched at runtime:

- **51,921 static world objects** — 24,359 ore nodes, 13,851 Pal spawn points,
  8,386 chests, 2,757 fishing spots, 2,163 dungeon objects, 220 NPC spawners and
  camps, 185 oil fields. Far too many to draw at once, so they are
  viewport-culled and capped at 2,000 markers.
- **All 396 effigies**, each with the instance GUID save files key on — which is
  what makes "which have I not found yet" answerable rather than just "here they
  all are".
- **174 fast-travel points**, validated 117/117 against a real player's unlocks —
  split into 8 tower bosses, 22 watchtowers and 144 ordinary points, because all
  174 drawn identically is why the towers looked missing.

Icons for Pals, items, elements and NPCs install from the reference archive:

```bash
python3 scripts/install-icons.py          # 1,409 icons, 7.7 MB
python3 scripts/install-icons.py --list   # what else is available
```

They are optional — without them every view renders text-only rather than a
column of broken images. Lookups go through a manifest rather than a guessed
path, because the sources disagree on capitalisation and a 404 reads as "this
Pal has no icon".

**Layers toggle per kind, not just per category** — each ore type, each chest
type, individually. And an Owner sets which categories each role may see, down to
whether a category is listed at all: on a server where finding things is the
point, handing every Player a complete ore map is a decision, not a default.

Coordinates match the in-game map, using a transform fitted to reference samples
(±0.5 map units). Note that in-game map X derives from world **Y**, and map Y
from world X — the axes swap.

**Items.** The Items tab totals every item across every container in the world —
base chests, guild chests, player inventories, palboxes. On the test world that's
645 item types and 8.3 million items across 11,639 containers. Totals are
computed during the parse, so opening the tab costs nothing.

**Base supply.** The Bases tab reports what each base is holding: its food boxes
and whether any are empty, its breeding farms and whether there is cake in them,
how many of its Pals are hungry, and how much of each staple material it has.

Two things it is careful about, and both are limits rather than features:

- **It reports facts, never mechanics.** The game's own
  `DT_MapObjectMasterDataTable` confirms these are distinct structures but says
  nothing about what any of them consumes — its columns are HP, defense and
  material type. So "this base has a Feed Box and it is empty" is reported and
  "move your food out of the chest" is not, because no file this project can read
  backs the second.
- **The threshold is yours, not the game's.** Every material stacks to 9,999, so
  "keep a stack at each base" would mean 110,000 Wood across eleven bases. The
  flag level is a control, shown next to the game's real stack size.

The **Guild Chest is listed separately, once per guild**, because that is what it
is — one 54-slot container shared by every base the guild owns. Two chests placed
at two bases are two doors into the same box.

**Who should be doing what.** The My Pals tab ranks your Pals for each of the
game's thirteen jobs, and by combat stats.

Work level is **read** — the species' own suitability plus any ranks bought with
Pal Souls, shown separately so you can see which is which. Work speed and the
combat stats are **calculated** from the game's formula and labelled as such.

You can pick an element to face, and each Pal gets a Strong / Weak / Neutral
badge. **It does not change the order, deliberately.** The game's files contain
the element *relation* but no damage multiplier — the one element constant in
them is `DamageElementMatchRate = 1.2`, and the widely quoted "2x dealt, half
taken" appears in no file. Ranking by a coefficient nobody has would look
authoritative and rest on nothing, so the stats decide the order and the matchup
sits beside it.

---

## Save editing

All of it is implemented and verified against a real world:

| What | Notes |
|---|---|
| Container sorting | Stackables-only or everything, world-wide or one base |
| Import / export | Versioned, checksummed documents; container import writes |
| Inventory slots | Set, change or clear any slot — including key items |
| Pal editor | Name, level, EXP, condenser rank, IVs, passive and active skills |
| Pal condition | Cure sickness, injury and hunger; set sanity, fullness, favourite slot, skin |
| Learned moves | The move *pool*, on Pals that carry one — separate from the three equipped |
| Bought work ranks | The work suitability bought with Pal Souls, per work type |
| Ownership history | Every previous owner — the only record of a trade a Pal has |
| Bulk Pal edits | One change set across many Pals, all-or-nothing |
| Pal duplication | Copy a Pal into a chosen palbox slot |
| **Pal import** | From a `pal` or `player` export — level, stars, skills, passives, IVs |
| Player editor | Name, level, EXP, technology and ancient points |
| Illegal-Pal check | Scan for out-of-range stats, repair by clamping |
| Coordinate teleport | Move a player to any point, or to one of the 174 fast-travel presets |
| World copy for co-op | Remap one player's uid across a **copy** of the world |

### Pal welfare

An affliction in Palworld is a **property that exists**: a healthy Pal carries no
`WorkerSick` field at all. So none of this was readable until it was looked for —
on a real 2,963-Pal world that is 54 sick, 97 hungry or starving, 21 injured and
33 with sanity under 50, none of which the dashboard could see.

The **My Pals** tab now leads with a welfare panel listing them worst-first, and
an operator with save-edit rights gets bulk cure, heal, feed and restore-sanity
buttons over the whole affected set.

Curing is a **deletion**, which is why it is safe: it produces a record identical
to a Pal that was never ill, rather than a "well" value this project invented.
Inflicting one is not offered.

Feeding writes **two** things. `HungerType` is a consequence of `FullStomach`,
so clearing the flag alone leaves the fullness where it was and the game sets it
straight back at the next tick — an edit you would watch succeed and then lose.
The fullness figure comes from the highest reading among your own affected Pals
and is shown before you press anything, because the real ceiling is per species
and per level and is stored nowhere in the save.

### Moving a character between servers

`soloexport.py` remaps a player's uid so a character works on another server or
in co-op. It is the **one save operation that never writes to the live world** —
it reads the world and produces a new directory, so it cannot corrupt anything
and does not require the server stopped.

Two departures from the reference implementation, both deliberate. It writes a
copy rather than mutating in place, and it **matches uids by value rather than by
key name**: the four named keys the reference rewrites miss **1,836 references**
on a real world, 1,817 of them `LastNickNameModifierPlayerUid` alone. A key list
is also a promise about a schema this project does not control, whereas a field
holding a player's uid *means* that player whatever it is called.

### Teleport

Coordinate teleport is a **save edit**, not a live command — so the server has to
be stopped. That limitation is real and worth stating plainly: it cannot unstick
a player who is stuck right now.

There is no live alternative. Verified against the shipped server binary rather
than community docs: the only teleport command is `TeleportToPlayerByIndex`, and
both admin teleports anchor to the **issuing admin's in-game character**. A
headless dashboard has no character in the world, so there is no anchor.

### Importing a Pal

Export a Pal (or a whole player, which includes their team) and import it back —
same file, unmodified. Two modes:

- **Overwrite** writes the file's values onto Pals already in the world, matched by
  instance id. Re-importing this world's own export is therefore a restore.
- **Add as a new Pal** creates one, by copying a same-species Pal already in the
  save and applying the file's fields. If you have never had that species, the
  import is refused and says so — a Pal's record carries values specific to the save
  it lives in, so one is copied rather than invented.

**The preview lists every field it will *not* write.** An export contains a Pal's
owner, container, slot and guild; none of those are editable. Dropping them silently
would let you believe an imported Pal changed hands, so they are shown with a reason
before you approve anything.

**Permissions are capabilities, not roles.** `save.sort.stackables`,
`save.sort.all` and `save.edit.full` are separate grants, enforced in the API
proxy *and* re-checked in the backend. `save.edit.full` additionally exists only
at `SECURITY_LEVEL=full`, so on a default install the editor is hidden even from
an Owner until someone deliberately raises the ceiling.

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
.venv/bin/python -m pytest      # backend: 991 tests, ~21 min
npm test                        # frontend: 82 tests, <1s
```

The suite is in tiers:

| Command | Tests | Time | Needs |
|---|---:|---:|---|
| `npm test` | 82 | <1 s | nothing |
| `pytest -m "not integration"` | 931 | ~2 min | nothing |
| `pytest` | 991 | ~21 min | `refworld/` + `palsav` |

**The 60 integration tests are ~19 of those 21 minutes.** Each parses a real
55 MB world, and the write paths take a full verified backup on top. That is the
cost of testing against a real save rather than a fixture, and it is worth
paying — but use `-m "not integration"` while iterating.

That is **11,571 lines of backend tests against 17,435 lines of backend code.**

Unit tests cover the corruption guard (every way it must refuse to write), path
handling, the settings-INI parser, the access-policy ceiling, the container sort
algorithm on synthetic data, password hashing, session revocation, login
throttling, and the role model. The frontend tests cover the proxy route
allowlist and the build-output tracing excludes. Integration tests run the real
pipeline against a real world: parse a 55 MB save, sort every container, write
it, re-read from disk and prove not one item moved in or out. They skip cleanly
when `refworld/` is absent, so a fresh checkout still runs green.

If you change anything under `backend/safety.py`, `backend/backup.py` or
`backend/saveedit.py`, run the full suite — the slow tests are the ones that
actually prove the save is safe.

**Two hazards this suite has actually hit**, both worth knowing before you add to
it:

- `vitest.config.ts` excludes `.next/`, because `next build` copies `src/` into
  `.next/standalone/` and vitest was discovering the stale copy — which would
  stay green against yesterday's build while the real source failed.
- Backend modules capture environment variables **at import time**, so tests
  monkeypatch the module attribute, not `os.environ`. For the same reason,
  patching `safety.assert_writable` does nothing to `backup.py`, which bound the
  name at import — a teleport test once passed for exactly that wrong reason.

The integration tests write full-world backup archives into `$TMPDIR`. If that is
a tmpfs, repeated interrupted runs will fill it; `/tmp` being full presents as
every shell command failing with no output.

---

## Reading the game's own files

`refs/palworld/` (a dedicated server install — gitignored, not shipped) unlocks
things the save files cannot answer, because the save only records what players
have *done*, never what exists.

`Pal-LinuxServer.pak` is **not encrypted**, and its entries use Oodle — which
this project already decompresses for saves. `scripts/palpak.py` lists and
extracts any of its 158,444 files.

Results so far:

- **The World Partition cell grid.** Cells are named `MainGrid_L0_X<col>_Y<row>`,
  and those names are coordinates. Cell size is 25,600 world units — measured,
  not guessed: at that value all 174 fast-travel points land inside an occupied
  cell. Connected components give one cluster per landmass, which is what finally
  pinned down the World Tree map's extent.
- **All 396 effigies**, each with its world position *and* the instance GUID
  that save files key on. That GUID is what makes "which have I not found yet"
  answerable rather than just "here they all are". `scripts/upackage.py` reads
  the package export map to pair each relic actor with its position;
  `scripts/extract-effigies.py` drives it. Bundled at 14 KB.

- **Every passive skill's actual numbers.** The bundled tables carry an English
  sentence — "Attack +5%" — which is right for showing a player and impossible to
  compute with, so the passive term in the stat formula was **zero on every Pal
  since the feature shipped**. `DT_PassiveSkill_Main` decodes out of the server
  pak with structured effect types, signed values, targets and invoke conditions;
  `scripts/extract-passive-effects.py` bundles all 1,897 at 20 KB. Cross-checked
  against the game's own prose: 1,754 of the 1,759 with a numeric description
  match exactly, and four of the five exceptions are the archive failing to
  substitute its own `{EffectValue1}` placeholder. On a real world this corrected
  **1,352 of 2,963 Pals**, the largest single attack figure by +1,515.

  A passive's bonus is **per stat**, not one number: `Legend` is +20% attack *and*
  +20% defence, `Noukin` is +30% attack and **−50%** work speed. 175 skills touch
  more than one stat and 77 carry a negative.

- **347 of the game's own tuning constants.** `BP_PalGameSetting`'s class-default
  object decodes out of the server pak — which supersedes the assumption that
  only DataTables come out of a pak, and is the answer to "surely that number is
  in the files somewhere". It usually is.

  **The decode verifies itself**: `CharacterMaxLevel` comes out **80** and
  `CharacterMaxRank` **5**, two constants this project already held from sources
  that explicitly could not be checked against the install. A misaligned read
  does not land two independently-known values in the right places, and
  `--verify` asserts them after a game update.

  Three numbers that had been guessed at were in there: the level cap above, the
  low-sanity threshold the welfare panel uses
  (`FriendshipPoint_AutoIncrementRequireSanity = 50`, which had been *chosen* at
  50), and the element damage multiplier.

Something it does **not** unlock, recorded so nobody searches twice:

- **Field boss levels.** Numeric properties in the unversioned block.

The element *relation* is also not in any file — all 480 DataTables were listed
and read, and no `Compatibility`, `Effectiveness` or `ElementDamage` asset exists
under any name. Its **multiplier**, however, is: see "Element matchups".

`DefaultPalWorldSettings.ini` from the same install is the authoritative list of
the 119 settings a 1.0 server accepts, and the test suite checks the parser and
presets against it.

### Element matchups — the one hand-entered thing here

Everything else in this project is extracted, and a script can re-derive it. The
element chart cannot be: it is in neither the game pak nor the reference archive
(see above), so it lives in C++ or in a blueprint's unversioned properties.

`backend/elements.py` therefore ships it as a **documented constant** with its
source named — the same footing as the level cap. It sits in a module rather than
in `backend/data/` on purpose, so it is never mistaken for extracted data.

**The game still decides what elements exist.** Only the *relation* is
hand-entered; the vocabulary is read off the bundled Pal data, and
`unknown_to_chart()` reports any element the game ships that the chart says
nothing about — because this is the one thing here that can silently rot, and a
tenth element would otherwise read as a confident "neutral" rather than a gap.

Two checks before it was trusted: the relation is **exactly reciprocal** (nine
strength pairs, nine weakness pairs, identical sets), and every name resolves
against the bundled Pals — eight of nine matched the source's spelling, with only
"Ground" needing mapping to the game's `Earth`.

**The multiplier is the game's, and it is not 2x.** Only the *relation* was
hand-entered; the number came out of `BP_PalGameSetting` —
`DamageElementMatchRate = **1.2**`. The widely repeated figure is 2x damage dealt
and half taken, and the game's settings object holds **exactly one**
element-damage constant with no halving counterpart, so neither popular number is
reproduced by the files.

The API still returns *strong*, *weak* or *neutral* rather than a damage
estimate, because that constant's meaning is inferred from its name and the
binary exports two more element-damage symbols that are C++ and unread.

---

## Licensing

**GPL-3.0-or-later**, and not by choice: `palsav` is GPL-3.0-or-later and is the
only thing that reads Palworld 1.0's Oodle-compressed saves.

**Private and LAN use requires nothing of you.** The obligations attach to
distribution, not to running it — and note that hosting it on the internet is
*not* distribution under GPL-3.0. See `docs/LICENSING.md`, which also covers the
likelier issue for any public release: the bundled game data and icons are
Pocketpair's.

---

## Known gaps

Being straight about what is not done:

- **The World Tree map is not calibrated, only measured.** Its extent now comes
  from the cell grid and is exact, but the image *orientation* has never been
  checked against a known point up there.
- **Dungeons are only partially mapped** — the save has 170 markers with state
  but no position. Extractable from the pak with the same technique the effigies
  and field bosses used; nobody has done it yet.
- **Raid bosses have no map presence, and cannot.** The 19 `RAID_` species are
  summoned at an altar rather than placed in the world, so a table of locations
  has nothing to say about them.
- **Non-Steam players are unverified.** The save carries the platform
  (`Steam`, `Xbox`, `PS5`, `Mac`) and the parser surfaces it, but no console
  player has ever been seen on the reference server, and **neither of the two
  reference projects handles this either** — so there is no prior art to copy.
  Everything treats a uid as an opaque string, which is why it is expected to
  work; expected is not verified. `docs/CROSSPLAY.md` has the three checks to run
  the day one joins.
- **Player and technology imports are refused**, with a reason. Container and Pal
  imports work.
- **Element matchups carry no damage multipliers**, and the chart behind them is
  the one piece of game data here that was hand-entered rather than extracted —
  it is in neither the pak nor the reference archive. See "Element matchups".
- **Field boss levels ARE available** as of this build — 90 placed bosses,
  levels 11-79, with verified world positions, on the map. This entry used to say
  the opposite; the levels were behind a table the pak reader was refusing.
- **No optimiser yet.** The inputs are now in place — Pal stats are correct for
  the first time since the passive term was fixed, work suitabilities and bought
  work ranks are readable — but nothing yet answers "who should mine".
- **No 2FA or password-reset flow**, and no dependency scanning in CI.
- **`docker` is not in the runtime image**, so the container Stop/Start buttons
  stay hidden unless you configure them. Deliberate — see "Maintenance mode".
  `docs/DEPLOYMENT.md` §4 has commands that work without it.

[pst]: https://github.com/deafdudecomputers/PalworldSaveTools
