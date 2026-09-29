import { test, expect } from '@playwright/test';
import { E2E_ADMIN_USER, E2E_PASSWORD } from './config.mjs';

test('all mined guides render actual bundled data without a world attached', async ({ page }) => {
  await page.goto('/');
  await page.getByPlaceholder('admin').fill(E2E_ADMIN_USER);
  await page.locator('input[type="password"]').fill(E2E_PASSWORD);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.locator('nav').getByRole('button', { name: 'Game guides', exact: true }).click();
  const picker = page.getByRole('combobox', { name: 'Guide', exact: true });
  const panel = page.locator('section').filter({ has: picker });
  await expect(picker.locator('option')).toHaveCount(12);
  const choices = await picker.locator('option').evaluateAll(options => options.map(o => (o as HTMLOptionElement).value));
  for (const choice of choices) {
    await picker.selectOption(choice);
    await expect(page.locator('table tbody tr').first()).toBeVisible();
    await expect(panel.getByRole('alert')).toHaveCount(0);
    await expect(page.locator('table')).not.toContainText('[object Object]');
  }
  await picker.selectOption('operating');
  await expect(page.getByRole('heading', { name: 'Operating Table', exact: true })).toBeVisible();
  await page.getByRole('textbox', { name: 'Search guide' }).fill('does-not-exist-123');
  await expect(page.getByText('0 matching entries', { exact: true })).toBeVisible();
  await page.getByText('Recorded supply events', { exact: true }).click();
  await expect(page.getByText('No supply state is available in the parsed save.')).toBeVisible();
});
