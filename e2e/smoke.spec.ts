import { test, expect, type Page } from '@playwright/test';
import { E2E_ADMIN_USER, E2E_PASSWORD } from './config.mjs';

// The stack is booted with nothing attached (e2e/serve.mjs): no world parsed,
// game server offline, no artwork. Every tab must still render a page rather
// than a crash — "the dashboard degrades to an honest empty state" is the
// property a fresh install depends on, and unit tests cannot see it.

async function signInAsOwner(page: Page) {
  await page.goto('/');
  await page.getByPlaceholder('admin').fill(E2E_ADMIN_USER);
  await page.locator('input[type="password"]').fill(E2E_PASSWORD);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByText('Server owner · Owner')).toBeVisible();
}

// A React render error replaces the page with Next's error boundary; an
// uncaught exception fires pageerror. Both are failures on every tab.
function collectPageErrors(page: Page) {
  const errors: string[] = [];
  page.on('pageerror', (err) => errors.push(err.message));
  return errors;
}

test('login page renders with a bootstrapped owner and guest access', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'paldash' })).toBeVisible();
  // The Owner was created from PANEL_PASSWORD at backend start, so the form
  // is live rather than showing the "no accounts exist" notice.
  await expect(page.getByText('No accounts exist yet')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeEnabled();
  await expect(page.getByRole('button', { name: 'Guest' })).toBeVisible();
});

test('a wrong password is refused and says so', async ({ page }) => {
  await page.goto('/');
  await page.getByPlaceholder('admin').fill(E2E_ADMIN_USER);
  await page.locator('input[type="password"]').fill('definitely-not-the-password');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByText(/incorrect|invalid|failed/i)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible();
});

test('the owner can open every tab with no world and no server', async ({ page }) => {
  const errors = collectPageErrors(page);
  await signInAsOwner(page);

  const nav = page.locator('nav .sidebar-btn');
  const count = await nav.count();
  expect(count).toBeGreaterThan(10);

  for (let i = 0; i < count; i++) {
    const button = nav.nth(i);
    const label = (await button.innerText()).trim();
    await button.click();
    await expect(page.getByRole('heading', { level: 1, name: label })).toBeVisible();
    await expect(page.getByText('Application error')).toHaveCount(0);
  }
  expect(errors, `uncaught errors while touring tabs:\n${errors.join('\n')}`).toEqual([]);
});

test('the server reads as offline and saves as read-only, not as a crash', async ({ page }) => {
  await signInAsOwner(page);
  await expect(page.getByText('Offline', { exact: true })).toBeVisible();
  // Nothing answers on the REST port and there is no save directory, so the
  // fail-closed rule must hold: the sidebar says read-only, never editable.
  await expect(page.getByText('Saves read-only')).toBeVisible();
  await expect(page.getByText('Saves editable')).toHaveCount(0);
});

test('a guest gets a reduced app and can sign out', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Guest' }).click();
  await expect(page.getByText('Guest', { exact: true })).toBeVisible();
  const tabs = await page.locator('nav .sidebar-btn').allInnerTexts();
  expect(tabs.map((t) => t.trim())).not.toContain('Users');
  expect(tabs.map((t) => t.trim())).not.toContain('Settings');
  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible();
});
