# Configuration reference

Every environment variable the dashboard reads, what its options mean, and what
changes if you set it. Generated against the source, so if a variable is missing
here it is missing from the code too.

`.env.example` is the working copy to edit. This file explains the choices.

**Two things behave differently from ordinary settings and are worth reading
first:**

- **`SECURITY_LEVEL` and the visibility settings are *ceilings*.** The web UI can
  lower them and never raise them. That is deliberate: the decision about whether
  this dashboard may write to your world is made on the host, by whoever has
  shell access, not in a browser by whoever got an Owner account.
- **Everything here is read once, at process start.** Changing a variable needs a
  container restart. Settings changed in the UI (Access tab) persist to
  `policy.json` and take effect immediately.

---

## 1. Accounts and sessions

| Variable | Default | What it does |
|---|---|---|
| `PANEL_PASSWORD` | *(none)* | Creates the first **Owner** on an empty database, then is ignored forever. Must meet `MIN_PASSWORD_LENGTH`. With no users and no value set, nobody can sign in and the log says so. |
| `PANEL_ADMIN_USER` | `admin` | Username for that first account. |
| `MIN_PASSWORD_LENGTH` | `10` | Rejected below this, on creation and on change. |
| `SESSION_TTL_HOURS` | `12` | How long a sign-in lasts. Sessions are server-side and revocable, so shortening this is belt-and-braces rather than the main control. |
| `AUDIT_RETENTION_DAYS` | `180` | How long audit entries are kept. The pruning run is itself audited. |

---

## 2. The write ceiling

### `SECURITY_LEVEL` — `readonly` · **`safe`** · `full`

The single most consequential setting. It gates *capabilities*, on top of
whatever a role grants — both gates must agree before anything is written.

| Value | What is permitted |
|---|---|
| `readonly` | Nothing may modify save files or server config. Backups can still be **created** (that only reads). |
| **`safe`** *(default)* | Backups and restores, `PalWorldSettings.ini` edits, and sorting of plain **stackable** items. Equipment is never moved, because equipment carries durability records that a sort could orphan. |
| `full` | Everything, including sorting equipment and the whole save editor — Pal and player editing, cloning, imports, slot edits, teleport. |

**Save editing is invisible at `safe`, even to an Owner.** That surprises people:
the editor is not hidden because of your role, it is hidden because the host says
so. The locked card in the UI names this variable.

### `ALLOW_UNVERIFIED_EDITS` — `true` · **`false`**

**Leave this false.** The corruption guard combines four signals and only permits
a write when the server is *provably* stopped; anything inconclusive (unreachable
REST API, unmounted volume, wrong password) resolves to "running" and blocks.

Setting `true` makes an *inconclusive* state count as stopped. That is how live
saves get corrupted, and a corrupted world is unrecoverable. It exists for
debugging, not for operation.

### `SAVE_READ_ONLY` — `true` · **`false`**

Never write to save files even when the server is provably stopped. Pair it with
mounting the game directory `:ro` — the mount is the guarantee, this is what
makes the UI say so instead of offering buttons that will fail.

---

## 3. Who sees what

All three use the same vocabulary: `everyone`, a **role name** meaning that rank
and above, or a sentinel. Roles, least to most privileged: `readonly`, `player`,
`trusted`, `moderator`, `admin`, `owner`.

### `DISCOVERY_VISIBILITY` — `everyone` · role name · **`trusted`** · `nobody`

Who sees fast-travel points and effigies **nobody has found yet**. Everyone
always sees their own discoveries; this only governs the undiscovered half.

- `everyone` — anyone who can see the map, including guests
- `<role>` — that rank and above see all; below it, only their own finds
- `nobody` — undiscovered locations are never sent to any session

Filtering happens server-side. A UI that received everything and hid some would
be handing out the answers in the network tab.

### `BASE_VISIBILITY` — `everyone` · role name · **`own`**

Who sees guild bases they are not a member of.

- `everyone` — every signed-in viewer sees every guild's bases
- `<role>` — that rank and above see all; below it, only their own guild's
- **`own`** *(default)* — everyone sees only their own guild's bases

**Moderators and above are always exempt**, so moderation works without an
exemption list.

This is separate from per-player privacy, and the distinction matters: privacy is
a *choice a player makes* and only protects **accounts**. Someone who has never
signed into the dashboard has no privacy setting at all, so before this existed
their bases were visible to every signed-in player regardless. This rule needs no
account to take effect.

### `WORLD_OBJECT_VISIBILITY` — JSON object, default `{}`

Per-category thresholds for the **35,687 static objects** extracted from the game
files — ore, chests, fishing spots, oil, spawners, dungeons. Same vocabulary,
per category:

```
WORLD_OBJECT_VISIBILITY={"ore":"trusted","treasure":"trusted","palspawner":"nobody"}
```

Categories: `ore`, `treasure`, `fishing`, `oilrig`, `palspawner`, `dungeon`.
Defaults are `trusted` for `treasure`, `palspawner` and `dungeon`; everything
else is `everyone`. A category withheld gets no layer toggle either — a visible
toggle that always returns nothing would itself disclose the category exists.

### Guest visibility

Guests are unauthenticated viewers; each toggle is independent.

| Variable | Default |
|---|---|
| `GUEST_VIEW_ENABLED` | `true` |
| `GUEST_SEE_SERVER_STATUS` | `true` |
| `GUEST_SEE_PLAYERS` | `true` |
| `GUEST_SEE_BASES` | `true` |
| `GUEST_SEE_GUILDS` | `true` |
| `GUEST_SEE_MAP_OBJECTS` | `false` |
| `GUEST_SEE_CHESTS` | `false` |
| `GUEST_SEE_ITEMS` | `false` |
| `GUEST_SEE_BREEDING` | `false` |

### Recipe: "only what players have actually found"

```bash
DISCOVERY_VISIBILITY=nobody
WORLD_OBJECT_VISIBILITY={"ore":"nobody","treasure":"nobody","fishing":"nobody","oilrig":"nobody","palspawner":"nobody","dungeon":"nobody"}
BASE_VISIBILITY=own
GUEST_VIEW_ENABLED=false
```

That leaves the map showing only each player's own discoveries and their own
guild's bases. Note the distinction: the *static* layers are the complete atlas
from the game files and have no per-player state, so the way to make them
"discovered only" is to withhold them entirely. Save-derived chests and nodes
still appear, because those are things your world has actually recorded.

---

## 4. Talking to the game server

| Variable | Default | Notes |
|---|---|---|
| `PALWORLD_REST_URL` | `http://127.0.0.1:8212` | Use the compose **service name**, e.g. `http://palworld:8212`. Never publish this port. |
| `PALWORLD_ADMIN_PASSWORD` | *(empty)* | Must match the server's `AdminPassword`. **A wrong value is worse than none:** the REST API answers 401, and the safety guard counts 401 as *running*, so save editing stays locked and you will not be told why. |
| `GAME_API_TIMEOUT_SECONDS` | `10` | Per request to the game's REST API. |
| `PALWORLD_BANLIST` | *(auto)* | Path to `banlist.txt`. Found beside the config by default. |
| `PALWORLD_CONFIG_INI` | *(auto)* | Path to `PalWorldSettings.ini`. Derived from `SAVE_BASE_DIR`. |
| `PALWORLD_INSTALL_DIR` | *(auto)* | The game install root, for build detection and mod detection. Inferred four levels above `SAVE_BASE_DIR`. Without it both honestly report "cannot tell" rather than guessing. |

---

## 5. Paths

| Variable | Default | Notes |
|---|---|---|
| `SAVE_BASE_DIR` | `/palworld/Pal/Saved/SaveGames/0` | The shared bind mount. |
| `WORLD_GUID` | *(auto)* | Pins a specific world when the save directory holds more than one. |
| `CACHE_DIR` | `/tmp/palworld-dashboard-cache` | **Put this on a volume.** It holds the SQLite database. |
| `DASHBOARD_DB` | `$CACHE_DIR/dashboard.db` | Accounts, sessions, audit log, metrics, schedules. |
| `POLICY_FILE` | `$CACHE_DIR/policy.json` | Persisted Access-tab settings. |
| `BACKUP_DIR` | `/palworld/backups` | **Move this off `/palworld`** — see `DEPLOYMENT.md`; it collides with jammsen's own backups and breaks under a read-only mount. |
| `SOLO_EXPORT_DIR` | `$BACKUP_DIR/exports` | World copies from the uid remap. Never auto-pruned. |
| `GAMEDATA_PATH`, `EFFIGY_DATA_PATH`, `WORLD_OBJECTS_PATH`, `HABITAT_DATA_PATH` | bundled | Only override to test a regenerated bundle. |

---

## 6. Backups

| Variable | Default | What it does |
|---|---|---|
| `BACKUP_KEEP_LATEST` | `5` | Newest N are always kept. |
| `BACKUP_KEEP_DAILY` | `7` | Then one per day for this many days. |
| `BACKUP_KEEP_WEEKLY` | `4` | Then one per week. |
| `BACKUP_MAX_TOTAL` | `50` | Hard ceiling. |
| `BACKUP_SAFETY_GRACE_HOURS` | `48` | Rollback points taken before an edit are protected this long — they are the only way back from a bad edit. |

Retention **thins** rather than truncates, and a missed schedule window is
skipped rather than replayed: a machine asleep for a week wakes and takes one
backup, not 168.

---

## 7. Parsing, and staying out of the game's way

| Variable | Default | What it does |
|---|---|---|
| `PARSE_ENABLED` | `true` | `false` disables `Level.sav` parsing entirely. Live REST features keep working. |
| `PARSE_AUTO` | `false` | Nothing parses on its own. A parse happens when someone presses Refresh. |
| `PARSE_MIN_INTERVAL_SECONDS` | `900` | Floor between parses. |
| `PARSE_TIMEOUT_SECONDS` | `600` | Hard kill for the parse subprocess. |
| `PARSE_INCLUDE_ITEMS` | `true` | Decode container contents. Costs time and memory; `false` loses the Items tab. |
| `PARSE_MAX_SIZE_MB` | `1024` | Refuse absurdly large saves rather than exhaust memory. |
| `PARSE_LOAD_AWARE` | `true` | Defer a parse when the game server is struggling. |
| `PARSE_MIN_SERVER_FPS` | `20` | Below this, a scheduled parse defers. |
| `PARSE_FORCE_MIN_SERVER_FPS` | `12` | A manual Refresh gets a lower floor — the operator asked and is watching. |

Load-aware throttling **fails open**, unlike the corruption guard: no data, a
stale sample, an unreachable server and a missing table all read as "fine to
parse". Refusing to write destroys nothing; refusing to parse forever merely
breaks the dashboard.

---

## 8. Safety probe tuning

| Variable | Default | What it does |
|---|---|---|
| `SAFETY_PROBE_TIMEOUT` | `3` | Seconds per probe before it counts as no answer — which resolves to **running**. |
| `SAVE_ACTIVITY_WINDOW_SECONDS` | `300` | A `.sav` written within this window means the server is alive. |
| `SAVE_READ_RETRIES` | `3` | Re-reads when a save looks torn. Not a retry of a failure: it distinguishes a partially-written file from a corrupt one. |

---

## 9. Metrics and scheduling

| Variable | Default | What it does |
|---|---|---|
| `METRICS_ENABLED` | `true` | Sampling on/off. |
| `METRICS_INTERVAL_SECONDS` | `60` | Sample period. |
| `METRICS_RETENTION_DAYS` | `30` | ~43,000 raw rows at the default interval, which SQLite answers instantly. |
| `GAME_BUILD_CHECK_INTERVAL_SECONDS` | `21600` (6 h) | How often to compare the installed game build against the bundled data. Two file reads, no network. Always runs once at startup. |

---

## 10. Controlling the server container

| Variable | Default | What it does |
|---|---|---|
| `STOP_COMMAND` | *(empty)* | Stops the game container. Unset hides the button rather than showing a broken one. |
| `START_COMMAND` | *(empty)* | Starts it again. |
| `RESTART_COMMAND` | *(empty)* | Restarts it. |
| `RESTART_COMMAND_TIMEOUT` | `120` | Abandon the command after this long. |
| `SERVER_RETURN_WATCH_SECONDS` | `180` | How long to watch for the server coming back after a shutdown. |

**Use the `node -e` form, not `docker`.** The Docker CLI is deliberately not in
the runtime image; `DEPLOYMENT.md` §4 has commands that work, and explains why
`304` must count as success.

---

## 11. Internals and limits

| Variable | Default | What it does |
|---|---|---|
| `BACKEND_HOST` | `127.0.0.1` | **Do not change.** The save backend must not be network-reachable. |
| `PYTHON_BACKEND_URL` | `http://127.0.0.1:8400` | Where the Next.js proxy finds the backend. Read by the proxy, not the backend. |
| `COOKIE_SECURE` | *(inferred)* | Forces the `Secure` flag on the session cookie. Left unset it is inferred from `X-Forwarded-Proto` or the request scheme, which is right for both plain-http LAN and a TLS reverse proxy. Set it only if your proxy does not send the header. |
| `BACKEND_PORT` | `8400` | Loopback port. |
| `MAX_UPLOAD_MB` | `64` | Ceiling on any upload. An unbounded read is a denial of service against the machine running your game server. |
| `PALWORLD_MAX_LEVEL` | `80` | The cap the editor validates against. 1.0 raised it from 65 — **not** the 100 rows in the EXP table, which carries headroom past the cap. |
| `BREEDING_MAX_DEPTH` | `4` | Breeding path search depth. |
| `BREEDING_MAX_FRONTIER` | `400` | Species considered per step. An unbounded walk over 46,655 pairs would eat the CPU the game needs. |
