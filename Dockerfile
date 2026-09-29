# ─── Stage 1: Build Next.js ──────────────────────────────
FROM node:22-bookworm-slim AS webbuilder

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

# ─── Stage 2: Build the Oodle (PlM) save decoder ─────────
# Palworld 1.0 compresses saves with Oodle Kraken. `palooz` is a C++ extension
# around the open-source `ooz` decoder and lives in a git submodule, so it has
# to be cloned with submodules and built — pip cannot fetch it directly.
#
# The Python minor version here MUST match the runtime stage's. `palooz` and
# `orjson` are compiled extensions, so their wheels are ABI-tagged (cp311 vs
# cp312) and pip refuses to install a mismatched one:
#   ERROR: orjson-...-cp312-...whl is not a supported wheel on this platform.
# Use the same upstream Python patch and Debian release in both stages.
# Debian bookworm's older distro Python has unresolved security advisories.
FROM python:3.11.16-slim-trixie AS pybuilder

RUN apt-get update && apt-get install -y --no-install-recommends \
        git build-essential \
    && rm -rf /var/lib/apt/lists/*

ARG PALSAV_REPO=https://github.com/deafdudecomputers/PalworldSaveTools.git
ARG PALSAV_REF=87fb4081d6b860778053ac0114754c8cae2b5f57

WORKDIR /build
RUN git init pst \
    && git -C pst remote add origin "${PALSAV_REPO}" \
    && git -C pst fetch --depth 1 origin "${PALSAV_REF}" \
    && git -C pst checkout --detach FETCH_HEAD \
    && test "$(git -C pst rev-parse HEAD)" = "${PALSAV_REF}" \
    && git -C pst submodule update --init --recursive --depth 1

RUN python -m pip install --no-cache-dir --upgrade pip build wheel \
    && python -m pip wheel --no-cache-dir --wheel-dir /wheels \
        ./pst/src/palsav/palooz \
        ./pst/src/palsav

COPY backend/requirements.txt backend/constraints.txt /tmp/
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels -r /tmp/requirements.txt

# ─── Stage 3: Runtime ────────────────────────────────────
FROM python:3.11.16-slim-trixie AS runner

RUN apt-get update && apt-get install -y --no-install-recommends \
        libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

# The standalone app needs the Node executable, not npm/corepack and their
# separate dependency trees. Keep package-management tooling in build stages.
COPY --from=webbuilder /usr/local/bin/node /usr/local/bin/node

# Run as a normal user, not root (audit S12). The container has your save
# directory bind-mounted, so root here is root over your world files.
#
# These must match the ownership of that bind mount, which is why they default
# to 1000:1000 — the same PUID/PGID the Palworld server image defaults to. If
# yours differ, build with --build-arg APP_UID=... rather than reverting to
# root. Reuse existing IDs if a future base image already defines them.
ARG APP_UID=1000
ARG APP_GID=1000
RUN set -eux; \
    test "${APP_UID}" -ne 0; \
    test "${APP_GID}" -ne 0; \
    getent group "${APP_GID}" >/dev/null || groupadd -g "${APP_GID}" app; \
    getent passwd "${APP_UID}" >/dev/null || \
        useradd -u "${APP_UID}" -g "${APP_GID}" -M -d /app -s /usr/sbin/nologin app

WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PYTHONUNBUFFERED=1 \
    CACHE_DIR=/app/cache

# Python deps (prebuilt wheels, so no compiler in the final image)
COPY --from=pybuilder /wheels /wheels
RUN python3 -m pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels

# Next.js standalone output
COPY --from=webbuilder --chown=${APP_UID}:${APP_GID} /app/.next/standalone ./
COPY --from=webbuilder --chown=${APP_UID}:${APP_GID} /app/.next/static ./.next/static
COPY --from=webbuilder --chown=${APP_UID}:${APP_GID} /app/public ./public

# Python backend
COPY --chown=${APP_UID}:${APP_GID} backend/ ./backend/
# The extraction/installer scripts, so the container can provision itself
# (#149): fetch artwork into the cache volume, and regenerate stale bundles
# from the server pak on the shared /palworld mount. Pure Python; the heavy
# lifting (palooz) is already installed for the backend.
COPY --chown=${APP_UID}:${APP_GID} scripts/ ./scripts/

COPY docker-entrypoint.sh /docker-entrypoint.sh
# Both of these are named-volume mount points. Docker seeds a fresh volume's
# ownership from the directory as it exists in the image, so they have to be
# created and chowned *here* — a non-root process cannot chown them later, and
# the backend would fail to open its SQLite database on first run.
RUN chmod +x /docker-entrypoint.sh \
    && mkdir -p /app/cache /app/backups \
    && chown "${APP_UID}:${APP_GID}" /app /app/cache /app/backups \
    && rm -f /usr/bin/mount /usr/bin/umount /usr/bin/nsenter /usr/bin/infocmp \
    && find /usr -xdev -type f -perm /6000 -exec chmod a-s {} +

USER ${APP_UID}:${APP_GID}

# Only the dashboard is published. The save backend stays on loopback.
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:3000/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

ENTRYPOINT ["/docker-entrypoint.sh"]
