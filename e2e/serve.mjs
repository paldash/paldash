#!/usr/bin/env node
// Boot the whole stack with nothing attached — no world, no game server, no
// palsav — the way a fresh install looks before anything is mounted. Both
// Playwright (webServer) and Lighthouse CI (startServerCommand) use this one
// launcher, so the two never test different environments.
//
// Everything lands in a scratch directory: a fresh SQLite database (so the
// Owner is bootstrapped from PANEL_PASSWORD on every run), an empty save
// directory (safety reads "unknown", which resolves to "running" — the
// fail-closed default the tests should see), and a REST URL pointing at a
// closed port so the game reads as offline rather than hanging on DNS.
import { spawn } from 'node:child_process';
import { cpSync, existsSync, mkdtempSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { BACKEND_PORT, WEB_PORT, E2E_PASSWORD } from './config.mjs';

// `output: "standalone"` is what the container runs, and `next start` refuses
// it with a warning. The standalone server does not carry the static chunks
// or public/ itself — the Dockerfile copies them alongside, so this does the
// same, or every stylesheet 404s and Lighthouse measures an unstyled page.
const standalone = join('.next', 'standalone');
if (!existsSync(join(standalone, 'server.js'))) {
  throw new Error('no standalone build — run `npm run build` first');
}
cpSync(join('.next', 'static'), join(standalone, '.next', 'static'), { recursive: true });
if (existsSync('public')) cpSync('public', join(standalone, 'public'), { recursive: true });

const scratch = mkdtempSync(join(tmpdir(), 'paldash-e2e-'));
for (const d of ['cache', 'backups', 'save']) mkdirSync(join(scratch, d), { recursive: true });

const python = process.env.E2E_PYTHON || 'python3';
const backend = spawn(python, ['backend/main.py'], {
  stdio: 'inherit',
  env: {
    ...process.env,
    DASHBOARD_DB: join(scratch, 'dashboard.db'),
    CACHE_DIR: join(scratch, 'cache'),
    BACKUP_DIR: join(scratch, 'backups'),
    SAVE_BASE_DIR: join(scratch, 'save'),
    PANEL_PASSWORD: E2E_PASSWORD,
    PALWORLD_REST_URL: 'http://127.0.0.1:9',
    METRICS_ENABLED: 'false',
    PARSE_AUTO: 'false',
    FETCH_ASSETS_ON_BOOT: 'false',
    DATA_REFRESH_ON_BOOT: 'off',
    BACKEND_PORT,
  },
});

const web = spawn(process.execPath, [join(standalone, 'server.js')], {
  stdio: 'inherit',
  env: {
    ...process.env,
    PORT: WEB_PORT,
    HOSTNAME: '127.0.0.1',
    PYTHON_BACKEND_URL: `http://127.0.0.1:${BACKEND_PORT}`,
    GUEST_VIEW_ENABLED: 'true',
  },
});

async function waitFor(url, label) {
  for (let i = 0; i < 240; i++) {
    try {
      const r = await fetch(url);
      if (r.status < 500) return;
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`${label} did not come up at ${url}`);
}

const stop = () => {
  backend.kill('SIGTERM');
  web.kill('SIGTERM');
  process.exit(0);
};
process.on('SIGINT', stop);
process.on('SIGTERM', stop);
backend.on('exit', (code) => {
  if (code !== null && code !== 0) {
    console.error(`backend exited with ${code}`);
    web.kill('SIGTERM');
    process.exit(code);
  }
});

await waitFor(`http://127.0.0.1:${BACKEND_PORT}/api/health`, 'backend');
await waitFor(`http://127.0.0.1:${WEB_PORT}/`, 'web');
console.log(`E2E_READY scratch=${scratch}`);
