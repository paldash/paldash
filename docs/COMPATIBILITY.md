# Server image compatibility

Which Palworld server images this dashboard works against, and where their layouts
differ.

Everything here was **read from the images' own published metadata**
(`skopeo inspect docker://…`, which fetches a few KB of config rather than the
multi-gigabyte image) rather than from their documentation or from memory. Two of
the findings below contradict what this project previously assumed.

Verified 2026-07-30.

## The images

| | `thijsvanloef/palworld-server-docker` | `jammsen/palworld-dedicated-server` |
|---|---|---|
| Bundled compose uses it | ✅ | — |
| Save path | `/palworld/Pal/Saved/SaveGames/0` | `/palworld/Pal/Saved/SaveGames/0` |
| Config path | `…/Pal/Saved/Config/LinuxServer` | `GAME_CONFIG_PATH`, same value |
| Install root | `/palworld` | `GAME_ROOT=/palworld` |
| Runs as | `PUID=1000` / `PGID=1000` | `PUID=1000` / `PGID=1000` |
| REST API port | `REST_API_PORT=8212` | `RESTAPI_PORT=8212` |
| RCON port | `RCON_PORT=25575` | `RCON_PORT=25575` |
| Regenerates the INI from env | **always** | **only when `SERVER_SETTINGS_MODE=auto`** |
| Own backup directory | — | `BACKUP_PATH=/palworld/backups` |

Both put the world where this dashboard's default `SAVE_BASE_DIR` expects it, and
both run as uid/gid 1000, which is why the dashboard image does the same — the
shared bind mount is readable without root.

## Different philosophies, and what each costs the dashboard

thijsvanloef ships **96** environment variables and jammsen **183**, and the split
says what each image is for.

**thijsvanloef — operations.** Auto-pause when empty, auto-reboot and auto-update on
cron expressions with player warnings, per-event Discord webhooks, player logging,
its own backup rotation, and ARM support via box64.

**jammsen — game settings.** Essentially every one of the 119 INI settings exposed
as a variable, plus randomizer support, a custom-script hook and player detection.

Two of those features change what the dashboard shows, both on thijsvanloef:

- **`AUTO_PAUSE_ENABLED` draws as an outage.** A paused server stops answering its
  REST API, so `metrics.py` records `reachable = 0` and the chart shades the span.
  That is the metric working as designed — a gap is data — but the cause is a pause,
  not a crash. Save writes stay correctly blocked throughout: a suspended process
  still exists, so `safety.py` sees it and refuses.
- **`AUTO_UPDATE_ENABLED` / `UPDATE_ON_BOOT` is the case `gameversion.py` is sized
  for.** The container updates the game and restarts, which is why the build check
  always runs once at startup rather than only on its interval.

Both images also keep their **own** backup directories (`OLD_BACKUP_DAYS` /
`BACKUP_RETENTION_POLICY`), which is why `collect_world_files` uses an explicit
include list — sweeping the server's rotating snapshots into a dashboard backup once
turned a 2.1 MB world into 66 MB archives.

## Two corrections this check produced

**jammsen does not always rewrite `PalWorldSettings.ini`.** This project previously
stated that both images "regenerate PalWorldSettings.ini from environment variables
on every start". The jammsen image ships **`SERVER_SETTINGS_MODE=manual`** as its
default, and in that mode it leaves the INI alone; only `auto` regenerates it. The
env-managed warning in the settings UI is now worded as a conditional rather than as
a fact about the operator's setup.

**The REST API port variable is spelled differently.** `REST_API_PORT` on
thijsvanloef, `RESTAPI_PORT` on jammsen. `settings_ini.ENV_MANAGED` names both, so
the warning points at something the operator will actually find in their compose
file.

## What the dashboard needs from any image

Nothing image-specific. The requirements are:

1. **The save directory bind-mounted** at `SAVE_BASE_DIR` (default
   `/palworld/Pal/Saved/SaveGames/0`). Read-only is fine for everything except save
   editing and restores.
2. **`RESTAPIEnabled=True`** and a reachable `PALWORLD_REST_URL`, for live status,
   the player list and admin commands. Without it the dashboard still parses saves;
   it just cannot see who is online or issue commands.
3. **A matching `PALWORLD_ADMIN_PASSWORD`.**
4. **uid/gid 1000 on the save files**, or a compatible `APP_UID`/`APP_GID` build arg.

Optional, and only for the features that need them:

- **The install root visible** (`PALWORLD_INSTALL_DIR`, or inferable four levels
  above `SAVE_BASE_DIR`) — for game-build detection and mod detection. Absent, both
  report "cannot tell" rather than guessing.
- **`banlist.txt` readable** beside `PalWorldSettings.ini` — for the ban list view.

## Re-running this check

```bash
skopeo inspect docker://docker.io/thijsvanloef/palworld-server-docker:latest
skopeo inspect docker://docker.io/jammsen/palworld-dedicated-server:latest
```

Look at `.Env` for the path and port variables. It costs a few KB — there is no need
to pull the images, and no need for a running daemon.

## Run-validated 2026-08-19 (#131) — executed, not inspected

Everything above was read from image metadata; this section is from actually
running both images (podman 5.8.2, rootless) against a copy of a real world
and driving the dashboard's own code at them. Game build v1.0.3.101283.

**INI regeneration, all four cells of the matrix, observed live:**

| | default | with the toggle |
|---|---|---|
| thijsvanloef | **regenerates** ("Using Env vars to create PalWorldSettings.ini") | `DISABLE_GENERATE_SETTINGS=true` → manual edit **survived** boot; `SERVER_NAME` env ignored |
| jammsen | `manual` — its own log: "NOT using environment variables"; manual edit survived, env ignored | `SERVER_SETTINGS_MODE=auto` → **regenerated** from env |

**The dashboard's write-verification cycle, both directions:** a `write_ini`
of `ServerName` followed by a restart of the regenerating image produced
verdict `reverted` with the warning naming `SERVER_NAME` as the variable to
set instead (#132's hint, live); the same write under the preserving toggle
produced `verified` with zero warnings.

**Safety:** with the server up, `editable: False` on three positive signals
(REST 200, port open, save written 25 s ago) — the game autosaves into the
mount, so `save_activity` is a real signal, not a formality. After a stop the
window aged out at 397 s and all three flipped; the process probe reports
`unknown (inconclusive)` because a containerised PalServer is outside the
dashboard's namespace, which is the fail-closed design degrading exactly as
documented.

**Save editing, end to end:** category sort with merge on the full world —
verified backup first, 4,932 containers touched, 14,321 slots changed,
conservation verified in memory and after re-read, 23 s. Then the strongest
check available anywhere: **the game booted the edited world** (day 704, all
16 bases, REST serving) and autosaved over it.

**Server stop/start (#108's fixed form):** `STOP_COMMAND`/`START_COMMAND`
set to `podman stop/start <name>` — the container went down and came back
through `lifecycle.run_stop_command`/`run_start_command`.

**Metrics honesty:** server up → `reachable: 1`; server down →
`reachable: 0` with `players`/`server_fps` **null, never 0**, host CPU and
disk still sampled.

Three deployment notes learned by running rather than reading:

- **jammsen refuses to start unless the container runs as root** (it drops to
  its own `steam` uid itself) and **aborts on default passwords** — set
  `SERVER_PASSWORD` and `ADMIN_PASSWORD` explicitly.
- Under **rootless podman**, run thijsvanloef with
  `--userns=keep-id:uid=1000,gid=1000` so its `PUID=1000` chown is a no-op;
  jammsen (which insists on container root) chowns the mount to a **subuid**,
  after which host-side processes lose write access — `podman unshare chown
  -R 0:0 <mount>` maps it back. Irrelevant when the dashboard runs as a
  container beside the game (both see uid 1000), which is the shipped layout.
- The activity window means "server stopped" becomes "editable" about **5
  minutes after** the last autosave — by design, and worth telling an
  operator watching the unlock.
