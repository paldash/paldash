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

## Game Pass / Xbox — extractable, but unverified

`backend/gamepass.py` reads the Windows Game Save (WGS) container tree Game Pass
writes instead of plain save files, and `scripts/extract-gamepass-save.py` drives it.

**It is a script, not a dashboard feature, and that is structural.** A Game Pass save
lives at `%LOCALAPPDATA%\Packages\PocketpairInc.Palworld_…\SystemAppData\wgs` on a
Windows PC. The dashboard runs in a container beside a Linux dedicated server and
cannot see that path, so a button for it would be one that can never work from the
machine the UI runs on.

**Nobody has run a real Game Pass save through it.** The format is derived from
`PalWorldSaveTools/xgp_save_extract.py`; the tests build a *synthetic* WGS tree to
the same understanding, so they prove the parser matches its spec rather than that
the spec is correct.

What makes shipping it defensible rather than reckless:

- It only **reads** the WGS tree and writes a fresh directory. No code path can
  touch an existing world.
- It **verifies every extracted file parses as GVAS** before keeping anything, and
  refuses a set with no `Level.sav`. A wrong offset therefore produces a named error,
  never a directory of plausible garbage.
- The integration tests wrap **real** `.sav` blobs from the reference world in a
  synthetic container, so the verification path and the round trip are exercised
  against genuine data — the extracted world parses with the ordinary reader and the
  bytes come out identical.
- A mid-sync container with two blob copies is **refused, not guessed**: picking the
  wrong one would restore a stale save over a current one.

If you have a Game Pass save, `--inspect` is read-only and costs nothing to try. If
it reports something odd, that is the format having changed, and the module needs
updating rather than the save.
