import { test, expect } from '@playwright/test';

test('public HTTP endpoints reject traversal and unused image optimization', async ({ request }) => {
  for (const path of ['/api/palworld/info/..%2Fplayers', '/api/palworld/settings', '/api/palworld/players/extra']) {
    const response = await request.get(path);
    expect(response.status(), path).toBe(404);
  }
  const image = await request.get('/_next/image?url=%2Ffavicon.ico&w=64&q=75');
  expect(image.status()).toBe(404);
});

test('HTML has framing and content-type protections and readiness reaches the backend', async ({ request }) => {
  const page = await request.get('/');
  expect(page.headers()['x-content-type-options']).toBe('nosniff');
  expect(page.headers()['content-security-policy']).toContain("frame-ancestors 'none'");
  expect((await request.get('/api/health')).status()).toBe(200);
});
