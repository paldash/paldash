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
FROM python:3.12-slim-bookworm AS pybuilder

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

WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PYTHONUNBUFFERED=1 \
    CACHE_DIR=/app/cache

# Python deps (prebuilt wheels, so no compiler in the final image)
COPY --from=pybuilder /wheels /wheels
RUN pip3 install --no-cache-dir --break-system-packages /wheels/*.whl && rm -rf /wheels

# Next.js standalone output
COPY --from=webbuilder /app/.next/standalone ./
COPY --from=webbuilder /app/.next/static ./.next/static
COPY --from=webbuilder /app/public ./public

# Python backend
COPY backend/ ./backend/

COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh \
    && mkdir -p /app/cache

# Only the dashboard is published. The save backend stays on loopback.
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:3000/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

ENTRYPOINT ["/docker-entrypoint.sh"]
