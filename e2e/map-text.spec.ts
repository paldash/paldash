import { test, expect } from '@playwright/test';
import { tooltipText } from '../src/lib/map-text';
import path from 'node:path';

test('Leaflet renders an attacker-controlled guild label as text', async ({ page }) => {
  await page.goto('about:blank');
  await page.addScriptTag({ path: path.resolve('node_modules/leaflet/dist/leaflet.js') });
  const attack = '<img src=x onerror="window.tooltipAttack=true">';
  const result = await page.evaluate(({ source, attack }) => {
    const makeText = new Function(`return (${source})`)() as (s: string) => HTMLElement;
    const leaflet = (window as unknown as { L: { tooltip: () => { setContent: (n: HTMLElement) => { getContent: () => HTMLElement } } } }).L;
    const content = leaflet.tooltip().setContent(makeText(attack)).getContent();
    document.body.append(content);
    return { text: content.textContent, images: content.querySelectorAll('img').length };
  }, { source: tooltipText.toString(), attack });
  expect(result).toEqual({ text: attack, images: 0 });
});
