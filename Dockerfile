# ─── Stage 1: Build Next.js ──────────────────────────────
FROM node:20-bookworm-slim AS webbuilder

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
# The runtime installs Debian bookworm's `python3`, which is 3.11 — so this is
# 3.11 too. Bumping one without the other breaks the build outright.
FROM python:3.11-slim-bookworm AS pybuilder

RUN apt-get update && apt-get install -y --no-install-recommends \
        git build-essential \
    && rm -rf /var/lib/apt/lists/*

ARG PALSAV_REPO=https://github.com/deafdudecomputers/PalworldSaveTools.git
ARG PALSAV_REF=main

WORKDIR /build
RUN git clone --depth 1 --branch "${PALSAV_REF}" --recurse-submodules "${PALSAV_REPO}" pst

RUN python -m pip install --no-cache-dir --upgrade pip build wheel \
    && python -m pip wheel --no-cache-dir --wheel-dir /wheels \
        ./pst/src/palsav/palooz \
        ./pst/src/palsav

COPY backend/requirements.txt /tmp/requirements.txt
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels -r /tmp/requirements.txt

# ─── Stage 3: Runtime ────────────────────────────────────
FROM node:20-bookworm-slim AS runner

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Run as a normal user, not root (audit S12). The container has your save
# directory bind-mounted, so root here is root over your world files.
#
# These must match the ownership of that bind mount, which is why they default
# to 1000:1000 — the same PUID/PGID the Palworld server image defaults to. If
# yours differ, build with --build-arg APP_UID=... rather than reverting to
# root. The base image already ships a `node` user at 1000, so reuse whatever
# is there instead of failing on a duplicate id.
ARG APP_UID=1000
ARG APP_GID=1000
RUN set -eux; \
    getent group "${APP_GID}" >/dev/null || groupadd -g "${APP_GID}" app; \
    getent passwd "${APP_UID}" >/dev/null || \
        useradd -u "${APP_UID}" -g "${APP_GID}" -M -d /app -s /usr/sbin/nologin app

WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PYTHONUNBUFFERED=1 \
    CACHE_DIR=/app/cache

# Python deps (prebuilt wheels, so no compiler in the final image)
COPY --from=pybuilder /wheels /wheels
RUN pip3 install --no-cache-dir --break-system-packages /wheels/*.whl && rm -rf /wheels

# Next.js standalone output
COPY --from=webbuilder --chown=${APP_UID}:${APP_GID} /app/.next/standalone ./
COPY --from=webbuilder --chown=${APP_UID}:${APP_GID} /app/.next/static ./.next/static
COPY --from=webbuilder --chown=${APP_UID}:${APP_GID} /app/public ./public

# Python backend
COPY --chown=${APP_UID}:${APP_GID} backend/ ./backend/

COPY docker-entrypoint.sh /docker-entrypoint.sh
# Both of these are named-volume mount points. Docker seeds a fresh volume's
# ownership from the directory as it exists in the image, so they have to be
# created and chowned *here* — a non-root process cannot chown them later, and
# the backend would fail to open its SQLite database on first run.
RUN chmod +x /docker-entrypoint.sh \
    && mkdir -p /app/cache /app/backups \
    && chown "${APP_UID}:${APP_GID}" /app /app/cache /app/backups

USER ${APP_UID}:${APP_GID}

# Only the dashboard is published. The save backend stays on loopback.
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:3000/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

ENTRYPOINT ["/docker-entrypoint.sh"]
