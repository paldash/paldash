import { test, expect } from '@playwright/test';
import { E2E_ADMIN_USER, E2E_PASSWORD } from './config.mjs';

test('server export requires a fresh preview of player and leader choices', async ({ page }) => {
  const a = '11111111-1111-1111-1111-111111111111';
  const b = '22222222-2222-2222-2222-222222222222';
  const c = '33333333-3333-3333-3333-333333333333';
  const guild = '44444444-4444-4444-4444-444444444444';
  const id = 'a'.repeat(32), hash = 'b'.repeat(64);
  const summary = { retainedPlayers: 2, removedPlayers: 1, removedGuilds: 0, removedBases: 0,
    removedCharacters: 10, removedContainers: 2, reassignedSharedReferences: 3 };
  let creations = 0;
  await page.route('**/api/save/players', route => route.fulfill({ json: [
    { uid: a, name: 'Aster' }, { uid: b, name: 'Birch' }, { uid: c, name: 'Cedar' },
  ] }));
  await page.route('**/api/save/export/server/preview', route => {
    const body = route.request().postDataJSON();
    expect(body.removeUids).toEqual([a]);
    return route.fulfill({ json: { artifactId: id, planHash: hash, ...summary,
      leaderChanges: [{ guildId: guild, newLeaderUid: body.leaders[guild] || b, retainedMembers: [b, c] }] } });
  });
  await page.route('**/api/save/export/server?background=true', route => {
    expect(route.request().postDataJSON()).toEqual({ artifactId: id, planHash: hash });
    creations++;
    return route.fulfill({ json: { jobId: id, state: 'queued' } });
  });
  let polls = 0;
  await page.route(`**/api/save/jobs/${id}`, route => {
    polls++;
    return route.fulfill({ json: { jobId: id, kind: 'server-export', state: polls === 1 ? 'running' : 'completed', stage: 'Verifying the serialized copy',
      result: { artifactId: id, sha256: hash, sizeBytes: 1024, ...summary }, error: null } });
  });
  await page.goto('/');
  await page.getByPlaceholder('admin').fill(E2E_ADMIN_USER);
  await page.locator('input[type="password"]').fill(E2E_PASSWORD);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.locator('nav').getByRole('button', { name: /Save tools/i }).click();
  await page.getByRole('button', { name: 'World export', exact: true }).click();
  const panel = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Export a pruned server world' }) });
  await expect(panel.getByRole('button', { name: 'Preview server copy' })).toBeDisabled();
  await panel.getByRole('checkbox', { name: /Aster/ }).check();
  await panel.getByRole('button', { name: 'Preview server copy' }).click();
  await expect(panel.getByRole('button', { name: 'Create this server copy' })).toBeEnabled();
  await panel.getByRole('combobox').selectOption(c);
  await expect(panel.getByRole('button', { name: 'Create this server copy' })).toBeDisabled();
  await panel.getByRole('button', { name: 'Preview updated choices' }).click();
  await panel.getByRole('button', { name: 'Create this server copy' }).click();
  await expect(panel.getByRole('link', { name: 'Download server world' })).toHaveAttribute('href', `/api/save/export/server/${id}/download`);
  expect(creations).toBe(1);
});
