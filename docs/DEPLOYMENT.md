# Deployment

How to run this beside a Palworld dedicated server. One container, two processes,
one shared bind mount.

`docker-compose.yml` in the repo root is a working reference. If you already have
a compose file for your server, copy the `dashboard:` service into it rather than
adopting the whole thing — the dashboard needs exactly three things from your
server, and nothing else about your setup has to change:

1. the **same host directory** mounted at the same place
2. a **shared Docker network**
3. `REST_API_ENABLED=true` on the game server

---

## 1. Which game-server image

Validated against **`thijsvanloef/palworld-server-docker`**. That is the only one
this has been run against end to end, and the honest statement is that the others
are expected to work rather than known to.

| Image | Notes |
|---|---|
| `thijsvanloef/palworld-server-docker` | What the reference compose file uses and what has actually been tested. `PUID`/`PGID` default to 1000. |
| `jammsen/docker-palworld-dedicated-server` | Same shape. Different env var names for game settings; the paths under `/palworld` are the same, which is all the dashboard reads. |
| A bare-metal / systemd server | Works. Point `SAVE_BASE_DIR` at the real path and skip the network entirely, since the REST API is then on the host. |

**The thing that actually differs between them matters more than the image
choice: whether the image regenerates `PalWorldSettings.ini` from environment
variables on every start.** Where it does, a setting the dashboard writes
survives until the next restart and is then silently reverted — worse than a
refusal, because you watched it work.

- **thijsvanloef: always regenerates.**
- **jammsen: only when `SERVER_SETTINGS_MODE=auto`.** It ships `manual`, so a
  default jammsen deployment leaves the INI alone.

`settings_ini.ENV_MANAGED` lists the keys commonly backed that way (under both
images' spellings — `REST_API_PORT` on thijsvanloef, `RESTAPI_PORT` on jammsen)
and the settings UI names them. The dashboard **cannot read your game
container's environment**, so this is worded as a conditional warning, not a
detection. If a setting keeps reverting, that is why: change it in `.env`.

`docs/COMPATIBILITY.md` has the full matrix, the other behavioural differences
that show up in the dashboard (auto-pause draws as an outage; auto-update is the
case the build check is sized for), and a one-line `skopeo inspect` command that
re-verifies all of it without pulling a multi-gigabyte image.

---

## 2. Ownership: match PUID/PGID or nothing is readable

The dashboard runs as **uid/gid 1000**, matching the Palworld server image's
`PUID`/`PGID` defaults, so it can read the shared bind mount without root. The
container has your world files mounted; root here would be root over your world.

If your server uses different ids, match them at build time — do not revert to
root:

```yaml
dashboard:
  build:
    context: .
    args: { APP_UID: "1001", APP_GID: "1001" }
```

`/app/cache` and `/app/backups` are created and chowned **inside the image**, not
at startup. Docker seeds a fresh named volume's ownership from the directory as it
exists in the image, and a non-root process cannot fix it afterwards — without
that, the backend cannot open its SQLite database on first run.

---

## 3. Read-only mode, if you want a hard guarantee

The corruption guard is thorough, but it is code. If you would rather have the
kernel enforce it:

```yaml
volumes:
  - ./palworld:/palworld:ro
environment:
  - SAVE_READ_ONLY=true
```

Backups and the parse cache are on their own volumes, so read-only leaves
everything working except restore and settings-editing. Set both: the `:ro` mount
is the guarantee, and `SAVE_READ_ONLY=true` is what makes the UI say so instead of
offering buttons that will fail.

---

## 4. Stop / start the game server from the dashboard

**This works, and the compose file's suggested commands do not.** Worth being
precise about, because the two facts have been recorded inconsistently.

Palworld's REST API can stop the game *process* but never start one, and knows
nothing about containers. So the full stop → edit saves → start cycle needs the
dashboard to reach Docker. It never gets the socket: a
`tecnativa/docker-socket-proxy` sidecar holds it and exposes only container
start/stop/restart.

The problem is that the commented-out commands in `docker-compose.yml` invoke
`docker`, and **the Docker CLI is not installed in the runtime image** — adding it
costs ~35 MB and was deliberately declined. So as written they fail with
`STOP_COMMAND not found: docker`.

They do not need the CLI. The socket proxy speaks the Docker HTTP API, and the
runtime image is `node:20-bookworm-slim`, so `node` with a global `fetch` is
already there — the healthcheck uses it. Use these instead:

```yaml
- STOP_COMMAND=node -e "fetch('http://docker-proxy:2375/containers/palworld-server/stop',{method:'POST'}).then(r=>process.exit(r.status<300||r.status===304?0:1)).catch(()=>process.exit(1))"
- START_COMMAND=node -e "fetch('http://docker-proxy:2375/containers/palworld-server/start',{method:'POST'}).then(r=>process.exit(r.status<300||r.status===304?0:1)).catch(()=>process.exit(1))"
- RESTART_COMMAND=node -e "fetch('http://docker-proxy:2375/containers/palworld-server/restart',{method:'POST'}).then(r=>process.exit(r.status<300||r.status===304?0:1)).catch(()=>process.exit(1))"
```

Three details that make this correct rather than merely plausible:

- **`304` counts as success.** The Docker API returns 204 when it stopped the
  container and **304 when it was already stopped**. Treating 304 as a failure
  would make "stop the server" report an error precisely when the server is
  already in the state you asked for.
- **It survives `shlex`.** `lifecycle._run_configured` splits the command with
  `shlex.split` and runs it through `subprocess.run` with **no shell**, so nothing
  can be injected — and the double-quoted script above splits into exactly three
  argv elements. Verified, not assumed.
- **Uncomment the `docker-proxy` service too**, and never publish its port.
  Anything that can reach it can start and stop containers.

**You do not have to enable this.** Stopping the `palworld` service does not touch
the dashboard container, so the dashboard stays up and unlocks save editing on its
own once the server is provably down:

```bash
docker compose stop palworld     # edit saves in the UI
docker compose start palworld
```

With no `STOP_COMMAND`/`START_COMMAND` set, the buttons are hidden rather than
shown-and-broken.

---

## 5. What is not published, and why

| Port | Published | Why |
|---|---|---|
| 3000 | yes | The UI. Put it behind a reverse proxy or VPN if it is not LAN-only. |
| 8400 | **no** | The save backend. It binds loopback inside the container. It authenticates for itself, but there is no reason to expose a second surface. |
| 8212 | **no** | The game's REST API. Palworld's own docs warn against exposing it; only the dashboard talks to it, over the private network. |
| 25575 | **no** | RCON. Not required — the dashboard uses REST for everything. |

---

## 6. Three traps in the container build

All three were invisible to the test suite and only appeared on a real build and
run. If you change the Dockerfile or the entrypoint, **build and run it** — do not
rely on tests.

- **The builder and runtime Python minor versions must match.** The runtime
  installs Debian bookworm's `python3`, which is **3.11**. `orjson` and `palooz`
  are compiled extensions, so a `python:3.12` builder produces cp312 wheels that
  pip refuses outright and the image does not build.
- **`docker-entrypoint.sh` is `#!/bin/bash`, not `sh`.** It uses `wait -n`, a
  bashism. Debian's `/bin/sh` is dash, which errors on it, and `set -e` then
  killed the container about a second after boot — every time, silently.
- **`.dockerignore` must exclude `refworld/` and `refs/`.** The first stage does
  `COPY . .`, so without it 132 MB including a real world save with real Steam IDs
  goes into the build context and the layer cache.

---

## 7. First run

```bash
cp .env.example .env      # then edit it
docker compose up -d --build
```

`PANEL_PASSWORD` creates the first **Owner** account on an empty database and is
ignored afterwards. Add everyone else from the Users tab so each person gets their
own login, their own role and their own line in the audit log.

`SECURITY_LEVEL` defaults to `safe`, which means **save editing is unavailable even
to an Owner** until you deliberately raise it to `full`. Environment variables are
a ceiling the web UI cannot raise, so this is a decision made on the host, not in
a browser.

Keep `.env` out of git. It holds your admin and server passwords, and
`settings_ini.SECRET_KEYS` masks them in the API and the audit log precisely
because they should never be reachable from the UI either.

---

## 8. Keeping it light

Gameplay wins over dashboard responsiveness. The defaults reflect that:

| Setting | Default | Effect |
|---|---|---|
| `PARSE_AUTO` | `false` | Nothing parses on its own. A parse happens when someone presses Refresh. |
| `PARSE_MIN_INTERVAL_SECONDS` | `900` | Floor between parses. |
| `PARSE_ENABLED` | `true` | Set `false` to disable `Level.sav` parsing entirely; the live REST features keep working. |
| `cpus` / `mem_limit` | `1.0` / `3g` | Hard ceiling, so the parser cannot starve the game. |

The parse runs in a **niced subprocess** with a hard timeout, and results persist
to disk — so restarting the container does not trigger a re-parse. Everything
derived from a parse is cached until the next one (`backend/viewcache.py`), so
repeated page loads cost nothing beyond serialisation.

---

## 9. Updating

Palworld ships a major update roughly every six months, which can move things this
project reads from the game files (map extents, effigy and ore coordinates, the
settings list, item and Pal tables).

The bundled data is **committed**, so an update does not break the dashboard — it
makes the bundled data stale. Regenerating needs `refs/`, which is not shipped:

```bash
python3 scripts/build-gamedata.py       # -> backend/data/gamedata.json.gz
python3 scripts/install-map-assets.py   # -> public/maps/*.webp
```

**Detection is built.** `backend/gameversion.py` polls the server install's Steam
`appmanifest` for its `buildid` — two file reads and a stat, no network — and the
dashboard shows a banner when the game's build has moved past the one the bundled
data was generated from. Build ids are monotonic, so it can tell an update from a
rollback rather than just "different".

The check is **self-rate-limited** (`GAME_BUILD_CHECK_INTERVAL_SECONDS`, default
6 h) and always runs once after startup, because the common case is a container
that auto-updated the game and restarted. If the install directory is not visible
— the normal deployment mounts only the save path — it reports "cannot tell"
rather than a reassuring "current".

`scripts/check-game-build.py` runs the same check from a shell.

**Never commit anything from `refs/palworld/`.** Besides the size, its
`PalWorldSettings.ini` holds live server passwords.

---

## 10. Shipping an image instead of building from a clone

**For your own servers: yes, and it is the better way to run this.** Nothing in
the image needs the repository at runtime. The runtime stage copies exactly four
things — the Next.js standalone bundle, its static assets, `public/`, and
`backend/` — and every byte of game data is already committed, so a running
container needs no clone, no `refs/`, and no network beyond your own server.

Build once on a machine that has the source, push, and pull everywhere else:

```bash
docker build -t ghcr.io/<you>/palworld-dashboard:1.0 .
docker push ghcr.io/<you>/palworld-dashboard:1.0
```

Tag a version rather than relying on `latest`: this image can write to your save
files, and "which build is running" is a question you want answerable during an
incident.

Then in `docker-compose.yml`, replace `build: .` with
`image: ghcr.io/<you>/palworld-dashboard:1.0`.

**With no registry at all**, which is often the right answer for one LAN box:

```bash
docker save ghcr.io/<you>/palworld-dashboard:1.0 | gzip > dashboard.tar.gz
# copy it over, then on the server:
gunzip -c dashboard.tar.gz | docker load
```

### Four things to settle before a *public* image

Private distribution — your own machines, your own registry — triggers none of
this. Publishing does.

**1. Pushing to a public registry is distribution under the GPL.** `palsav` is
GPL-3.0-or-later and this project inherits it (`docs/LICENSING.md`). So a public
image obliges you to offer the **complete corresponding source** — the dashboard,
your changes, and the exact `palsav`/`palooz` revisions the binary was built
from — under the same licence. In practice: publish the repository, and point at
it from the image itself so the offer travels with the artifact.

```dockerfile
LABEL org.opencontainers.image.source="https://github.com/<you>/palworld-dashboard" \
      org.opencontainers.image.licenses="GPL-3.0-or-later"
```

`LICENSE` is already inside the image (it sits at the root of the standalone
bundle), which is the other half of the requirement.

Note the asymmetry that catches people out: **hosting the dashboard publicly is
not distribution** — that would be the AGPL. Handing over the software is.

**2. The bundled game data is Pocketpair's, and it is the likelier problem.**
`gamedata.json.gz`, `worldobjects.json.gz`, `effigies.json.gz` and the two 8192 px
map textures are extracted from the game's own assets. The MIT licence on the
packaging covers the packager's compilation work, not Pocketpair's underlying IP.
Every community wiki and planner does the same thing and it is fine for a private
tool — but a public image ships those assets to everyone who pulls it, which is a
different act from using them yourself. Settle this separately from the code
licence; it is not answered by going GPL.

**3. `PALSAV_REF` defaults to `main`, so builds are not reproducible.** The
Dockerfile clones `PalworldSaveTools` at build time:

```dockerfile
ARG PALSAV_REF=main
```

`main` moves. Two images built a week apart from the same commit of *this* repo
can contain different parsers, and a GPL source offer has to correspond to the
binary you actually shipped. Pin a commit SHA before publishing:

```bash
docker build --build-arg PALSAV_REF=<sha> -t … .
```

This also means **the build needs network access** to GitHub, while the runtime
does not.

**4. `APP_UID`/`APP_GID` are build args, so a published image is fixed at
1000:1000.** That matches both server images' `PUID`/`PGID` defaults, so it is
right for most people. If yours differ, a pulled image does not need rebuilding —
override at run time:

```yaml
dashboard:
  image: ghcr.io/<you>/palworld-dashboard:1.0
  user: "1001:1001"
```

**The one catch**: `/app/cache` and `/app/backups` are chowned to 1000 *in the
image*, and Docker seeds a fresh named volume's ownership from it. A 1001 process
then cannot write its SQLite database. Either chown the volumes once after
creating them, or bind-mount host directories you already own instead of using
named volumes.

### Multi-architecture

`palooz` and `orjson` are compiled extensions, so each architecture is a real
compile rather than a repack. `buildx` handles it, slowly:

```bash
docker buildx build --platform linux/amd64,linux/arm64 \
  -t ghcr.io/<you>/palworld-dashboard:1.0 --push .
```

Worth doing only if you actually run arm64 — a Raspberry Pi or an Ampere VPS.
Note that Palworld's own dedicated server has no native arm64 build;
thijsvanloef runs it under box64, so an arm64 dashboard image is for a
split deployment rather than the usual one-box setup.

### Building with podman

`podman build` works on the same Dockerfile with no changes — useful because it
needs no root and no daemon socket. Verified 2026-07-30: **379 MB image, builds
and runs**, container stays up, runs as `uid=1000(node)`, dashboard answers 200,
the backend is reachable on loopback inside and refused from the host, sign-in
works and a wrong password gives 401.

**One difference worth knowing:** podman defaults to the OCI image format, which
has no `HEALTHCHECK` field, so it prints

```
level=warning msg="HEALTHCHECK is not supported for OCI image format and will be ignored. Must use `docker` format"
```

and drops it silently. The image still works; it just has no healthcheck, so
compose `depends_on: condition: service_healthy` will never be satisfied. Pass
`--format docker` if you want it kept:

```bash
podman build --format docker -t palworld-dashboard:latest .
```

### What is *not* a reason to publish an image

The build is the only slow part and it happens once. If the goal is just "I do
not want a clone on my server", `docker save` / `docker load` gets you there
without a registry, an account, or any of the licensing questions above.
