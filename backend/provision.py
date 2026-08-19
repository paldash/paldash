"""
Self-maintaining boot (#149): artwork fetched if absent, bundles rebuilt if
the game moved — checked on every spin-up, so neither ever becomes a command
somebody has to know about.

Two independent provisioners, both fail-soft and both run in background
threads from the lifespan hook:

**Artwork.** The game's icons and map textures are the one thing the repo
does not distribute (PUBLISHING.md option B, the operator's call). If
`public/icons` and `public/maps` are already populated — a dev checkout, or
an image built with them — this is a no-op. Otherwise, with
`FETCH_ASSETS_ON_BOOT` enabled (the default), the PalworldSaveTools archive
(~27 MB) is downloaded once into the cache volume and the two installers run
— measured at under a second of CPU. A container with no network keeps
working: the UI shows text and shape fallbacks everywhere, and the banner
says the artwork is not installed rather than leaving blanks to read as
bugs.

**Bundles.** `gameversion.status()` already knows when the installed game
build no longer matches what the data bundles were built from. The default
compose mounts the game's whole install, so the server pak the operator is
actually running sits at a known path — and every bundle carries a
`regenerateWith` command that `scripts/regenerate-bundles.py` orchestrates.
When the build is stale and `DATA_REFRESH_ON_BOOT` is `auto` (the default),
that pipeline runs once per detected build, niced, in the background; the
refreshed bundles are copied into the cache volume so the next boot starts
from them (the entrypoint overlays them onto `backend/data/` before the
backend imports anything).

**One attempt per build, recorded either way.** A game update can change
formats such that an extractor refuses — which is those scripts working as
designed — and retrying every boot would hammer a machine that also runs the
game. The stamp keeps the outcome, the banner reports it, and a dashboard
update (which may carry fixed extractors) clears the way for a new attempt
because the stamp is keyed on build + dashboard data-schema.

Nothing here touches a save file, and nothing here blocks the app: a
provisioner that hangs costs a thread, not the dashboard.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from typing import Any, Optional

logger = logging.getLogger("provision")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

CACHE_DIR = os.environ.get("CACHE_DIR", "/tmp/palworld-dashboard-cache")
PROVISION_DIR = os.path.join(CACHE_DIR, "provision")

PUBLIC_DIR = os.environ.get("PUBLIC_DIR", os.path.join(_ROOT, "public"))
SCRIPTS_DIR = os.path.join(_ROOT, "scripts")

FETCH_ASSETS = os.environ.get("FETCH_ASSETS_ON_BOOT", "true").lower() == "true"
#: `auto` rebuilds when stale; anything else disables. There is no "force"
#: — rerunning is `rm` of the stamp, documented rather than automated.
DATA_REFRESH = os.environ.get("DATA_REFRESH_ON_BOOT", "auto").lower()
ASSET_ARCHIVE_URL = os.environ.get(
    "ASSET_ARCHIVE_URL",
    "https://github.com/deafdudecomputers/PalworldSaveTools/archive/refs/heads/main.zip",
)
_DOWNLOAD_TIMEOUT = int(os.environ.get("ASSET_DOWNLOAD_TIMEOUT", "300"))

#: Bumped when the asset-install pipeline itself changes shape, so existing
#: installs re-run once. Presence alone cannot express "our installer got
#: smarter".
ASSETS_SCHEMA = 1

_state: dict[str, Any] = {
    "assets": {"state": "unchecked"},
    "bundles": {"state": "unchecked"},
}
_lock = threading.Lock()


def state() -> dict[str, Any]:
    with _lock:
        return json.loads(json.dumps(_state))


def _set(section: str, **kw: Any) -> None:
    with _lock:
        _state[section] = kw
    logger.info("provision %s: %s", section, kw.get("state"))


# ─── Artwork ─────────────────────────────────────────────


def _artwork_installed() -> bool:
    icons = os.path.join(PUBLIC_DIR, "icons", "items")
    maps = os.path.join(PUBLIC_DIR, "maps", "palpagos.webp")
    try:
        return os.path.exists(maps) and len(os.listdir(icons)) > 100
    except OSError:
        return False


def _manifest_path() -> str:
    return os.path.join(PROVISION_DIR, "assets-manifest.json")


def _manifest_current() -> bool:
    try:
        with open(_manifest_path(), encoding="utf-8") as f:
            return json.load(f).get("schema") == ASSETS_SCHEMA
    except (OSError, ValueError):
        # No manifest but artwork present: a dev checkout or an image that
        # baked the assets. Presence wins — the manifest exists to force a
        # re-run when OUR pipeline changes, not to disown files it did not
        # install.
        return _artwork_installed()


def _download(url: str, dest: str) -> None:
    tmp = dest + ".part"
    with urllib.request.urlopen(url, timeout=_DOWNLOAD_TIMEOUT) as resp, \
            open(tmp, "wb") as out:
        shutil.copyfileobj(resp, out)
    os.replace(tmp, dest)


def _run_installer(script: str, archive: str, out_dir: str) -> None:
    done = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, script),
         "--archive", archive, "--out", out_dir],
        capture_output=True, text=True, timeout=600,
    )
    if done.returncode != 0:
        raise RuntimeError(
            f"{script} exited {done.returncode}: "
            f"{(done.stdout + done.stderr).strip()[-400:]}")


def ensure_artwork() -> None:
    if _artwork_installed() and _manifest_current():
        _set("assets", state="installed")
        return
    if not FETCH_ASSETS:
        _set("assets", state="disabled",
             note="FETCH_ASSETS_ON_BOOT=false and no artwork installed")
        return

    os.makedirs(PROVISION_DIR, exist_ok=True)
    archive = os.path.join(PROVISION_DIR, "palworldsavetools.zip")
    try:
        # A refs/ copy beats a download — dev checkouts and airgapped
        # deployments that pre-seeded the cache volume both land here.
        local = os.path.join(_ROOT, "refs", "PalWorldSaveTools-main.zip")
        if not os.path.exists(archive):
            if os.path.exists(local):
                archive = local
            else:
                _set("assets", state="fetching", url=ASSET_ARCHIVE_URL)
                _download(ASSET_ARCHIVE_URL, archive)

        _run_installer("install-icons.py", archive,
                       os.path.join(PUBLIC_DIR, "icons"))
        _run_installer("install-map-assets.py", archive,
                       os.path.join(PUBLIC_DIR, "maps"))

        # Persist into the cache volume: the image's /app/public is
        # ephemeral, and the entrypoint restores these on the next boot
        # before anything serves them. A dev checkout (PUBLIC_DIR inside the
        # repo) gets the same copy harmlessly.
        for kind in ("icons", "maps"):
            src = os.path.join(PUBLIC_DIR, kind)
            dst = os.path.join(PROVISION_DIR, f"public-{kind}")
            if os.path.isdir(src):
                shutil.copytree(src, dst, dirs_exist_ok=True)

        with open(_manifest_path(), "w", encoding="utf-8") as f:
            json.dump({"schema": ASSETS_SCHEMA, "installedAt": time.time(),
                       "source": archive}, f)
        _set("assets", state="installed", fetched=True)
    except Exception as e:  # noqa: BLE001 — fail soft is the whole contract
        # The dashboard runs fine without artwork; the banner carries the
        # reason so a blank map reads as a state, not a bug. Next boot
        # retries (no stamp on failure) — a transient network error should
        # not require anyone to know a command.
        _set("assets", state="failed", error=str(e)[:300])


# ─── Bundles ─────────────────────────────────────────────


def _link_refs() -> None:
    """
    Point the dev-checkout paths the extraction scripts default to at what
    the container actually has: `refs/palworld` -> the /palworld mount, and
    the PST archive -> the copy the artwork fetch put in the cache volume.
    Symlinks, created only where the target exists and the link is absent, so
    a real dev checkout is never touched.
    """
    refs = os.path.join(_ROOT, "refs")
    try:
        os.makedirs(refs, exist_ok=True)
        game = os.path.join(refs, "palworld")
        if not os.path.exists(game) and os.path.isdir("/palworld"):
            os.symlink("/palworld", game)
        zip_link = os.path.join(refs, "PalWorldSaveTools-main.zip")
        cached = os.path.join(PROVISION_DIR, "palworldsavetools.zip")
        if not os.path.exists(zip_link) and os.path.exists(cached):
            os.symlink(cached, zip_link)
    except OSError as e:
        logger.warning("provision: could not link refs/: %s", e)


def _pak_path() -> Optional[str]:
    for path in (
        os.path.join(_ROOT, "refs", "palworld", "Pal", "Content", "Paks",
                     "Pal-LinuxServer.pak"),
        "/palworld/Pal/Content/Paks/Pal-LinuxServer.pak",
    ):
        if os.path.exists(path):
            return path
    return None


def _stamp_path(build: str) -> str:
    import parse_worker

    return os.path.join(
        PROVISION_DIR, f"refresh-{build}-s{parse_worker.SCHEMA_VERSION}.json")


def _persist_dir() -> str:
    return os.path.join(PROVISION_DIR, "bundles")


def ensure_bundles() -> None:
    import gameversion

    if DATA_REFRESH != "auto":
        _set("bundles", state="disabled")
        return

    status = gameversion.status()
    build = str(status.get("buildId") or "")
    verdict = status.get("verdict")
    if verdict != "stale":
        # `unknown` is not `current` — gameversion's own rule — and the
        # banner must not claim freshness nobody verified.
        _set("bundles", state="current" if verdict == "current" else "unknown",
             build=build)
        return
    if not build:
        _set("bundles", state="unknown-build",
             note="installed build unreadable; cannot key a refresh")
        return

    stamp = _stamp_path(build)
    if os.path.exists(stamp):
        try:
            with open(stamp, encoding="utf-8") as f:
                prior = json.load(f)
        except (OSError, ValueError):
            prior = {}
        _set("bundles", state="attempted", build=build,
             ok=prior.get("ok"), failed=prior.get("failed"))
        return

    pak = _pak_path()
    if pak is None:
        _set("bundles", state="no-pak", build=build,
             note="game install not mounted; bundled data stays as shipped")
        return

    _set("bundles", state="rebuilding", build=build)
    os.makedirs(PROVISION_DIR, exist_ok=True)
    _link_refs()
    before = {f: os.path.getmtime(os.path.join(_DATA_DIR, f))
              for f in os.listdir(_DATA_DIR)}
    try:
        done = subprocess.run(
            ["nice", "-n", "15", sys.executable,
             os.path.join(SCRIPTS_DIR, "regenerate-bundles.py")],
            cwd=_ROOT, capture_output=True, text=True, timeout=7200,
            env={**os.environ, "PALWORLD_PAK": pak},
        )
        ok = done.returncode == 0
        tail = (done.stdout + done.stderr).strip()[-600:]
    except (OSError, subprocess.TimeoutExpired) as e:
        ok, tail = False, f"{type(e).__name__}: {e}"

    # Persist whatever DID regenerate — a partial refresh of correct bundles
    # is strictly better than none, and each script verifies its own output
    # or refuses, so "written" means "passed its own checks".
    changed: list[str] = []
    os.makedirs(_persist_dir(), exist_ok=True)
    for fname in os.listdir(_DATA_DIR):
        path = os.path.join(_DATA_DIR, fname)
        if os.path.isfile(path) and os.path.getmtime(path) > before.get(fname, 0):
            shutil.copy2(path, os.path.join(_persist_dir(), fname))
            changed.append(fname)

    with open(stamp, "w", encoding="utf-8") as f:
        json.dump({"ok": ok, "changed": sorted(changed), "log": tail,
                   "at": time.time()}, f)

    if changed:
        _reload_consumers()
    _set("bundles", state="rebuilt" if ok else "partial",
         build=build, changed=len(changed),
         **({} if ok else {"note": "some extractors refused; see cache stamp"}))


def _reload_consumers() -> None:
    """Bundle-file caches mostly self-invalidate (`viewcache.per_file` keys on
    size+mtime); the two module-level caches with documented reloads get them
    called. Best-effort: a reload that throws must not kill the thread."""
    for mod_name, fn in (("gamedata", "reload"), ("worldobjects", "reload"),
                         ("habitats", "reload")):
        try:
            mod = __import__(mod_name)
            getattr(mod, fn)()
        except Exception:  # noqa: BLE001
            logger.warning("provision: %s.%s() failed", mod_name, fn)


def boot() -> None:
    """Spawn both provisioners. Called once from the lifespan hook; daemon
    threads so a hung download can never hold up shutdown."""
    threading.Thread(target=ensure_artwork, daemon=True,
                     name="provision-assets").start()
    threading.Thread(target=ensure_bundles, daemon=True,
                     name="provision-bundles").start()
