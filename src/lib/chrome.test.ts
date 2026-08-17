/**
 * The chrome-translation packs — tested against the SHIPPED files, the same
 * rule the backend's bundle tests follow: a fixture would pin the loader and
 * let the packs regress underneath it.
 *
 * The two claims that matter:
 *  - provenance travels in every pack (the labelled-beta contract), and
 *  - no machine pack carries a safety-critical string — a mistranslated
 *    "The server must be stopped first" can cost someone a world, so those
 *    stay visibly English until a human verifies them.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

import { t } from './chrome';

const LANG_DIR = join(__dirname, 'chrome-langs');
const WRAPPED = JSON.parse(
  readFileSync(join(__dirname, '..', '..', 'docs', 'chrome-wrapped.json'), 'utf-8'),
);

function packs(): { code: string; pack: Record<string, unknown> }[] {
  return readdirSync(LANG_DIR)
    .filter((f) => f.endsWith('.json'))
    .map((f) => ({
      code: f.replace(/\.json$/, ''),
      pack: JSON.parse(readFileSync(join(LANG_DIR, f), 'utf-8')),
    }));
}

describe('chrome packs', () => {
  it('t() is identity with no pack loaded', () => {
    expect(t('Loading…')).toBe('Loading…');
    expect(t('not a catalogued string')).toBe('not a catalogued string');
  });

  it('every shipped pack declares its provenance', () => {
    for (const { code, pack } of packs()) {
      expect(pack.language, code).toBe(code);
      expect(['machine', 'human', 'game'], code).toContain(pack.provenance);
      expect(typeof pack.verified, code).toBe('boolean');
      expect(pack.strings && typeof pack.strings, code).toBe('object');
    }
  });

  it('no machine pack carries a safety-critical string', () => {
    const safety: string[] = WRAPPED.safetyCritical;
    expect(safety.length).toBeGreaterThan(0);
    for (const { code, pack } of packs()) {
      if (pack.provenance !== 'machine') continue;
      const strings = pack.strings as Record<string, string>;
      for (const s of safety) {
        expect(strings[s], `${code} must not translate: ${s}`).toBeUndefined();
      }
    }
  });

  it('machine packs are labelled unverified until a human promotes them', () => {
    for (const { code, pack } of packs()) {
      if (pack.provenance === 'machine') {
        expect(pack.verified, code).toBe(false);
      }
    }
  });

  it('the game-provided strings are the game’s, not the machine’s', () => {
    // Every non-en pack lists which of its strings came from the game's own
    // UI tables; those keys must actually be present in the dictionary.
    for (const { code, pack } of packs()) {
      if (code === 'en') continue;
      const provided = (pack.gameProvided ?? []) as string[];
      const strings = pack.strings as Record<string, string>;
      for (const key of provided) {
        expect(strings[key], `${code}: ${key}`).toBeTruthy();
      }
    }
  });

  it('all sixteen languages ship', () => {
    expect(packs().length).toBe(16);
  });
});
