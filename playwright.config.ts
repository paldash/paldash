import { defineConfig } from '@playwright/test';
import { BASE_URL } from './e2e/config.mjs';

// Browser smoke tests against the real stack (see e2e/serve.mjs). Needs a
// built app: `npm run build` first, then `npx playwright test`.
export default defineConfig({
  testDir: 'e2e',
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: BASE_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
  webServer: {
    command: 'node e2e/serve.mjs',
    url: `${BASE_URL}/`,
    timeout: 180_000,
    reuseExistingServer: !process.env.CI,
    stdout: 'ignore',
    stderr: 'pipe',
  },
});
