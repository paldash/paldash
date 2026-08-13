import { describe, it, expect } from 'vitest';
import { localName, matchesQuery, DEFAULT_LANG, type LanguagePack } from './language';

const de: LanguagePack = {
  lang: 'de',
  names: {
    pals: { alpaca: 'Melpaca', sheepball: 'Lamball' },
    items: { accessory_normalresist_1: 'Neutralschutzring' },
  },
};

describe('localName', () => {
  it('resolves an id to the localised name', () => {
    expect(localName(de, 'pals', 'Alpaca', 'Melpaca-en')).toBe('Melpaca');
  });

  it('matches ids case-insensitively, like every other lookup here', () => {
    // The upstream data is inconsistently capitalised — a save writes
    // `Sheepball` where the tables say `SheepBall` — so an exact match silently
    // loses real Pals.
    expect(localName(de, 'pals', 'SheepBall', 'x')).toBe('Lamball');
    expect(localName(de, 'pals', 'sheepball', 'x')).toBe('Lamball');
  });

  it('falls back rather than showing an id when there is no entry', () => {
    expect(localName(de, 'pals', 'NewSpeciesFromAnUpdate', 'Fallback')).toBe('Fallback');
    expect(localName(null, 'pals', 'Alpaca', 'Melpaca')).toBe('Melpaca');
    expect(localName(de, 'pals', '', 'Fallback')).toBe('Fallback');
  });

  it('keeps item tiers apart', () => {
    // `item_name_accessory_normalresist_1` -> `accessory_normalresist_1`. The
    // tier is part of the id, so a prefix strip preserves what the game
    // distinguishes and the third-party archive does not.
    expect(localName(de, 'items', 'Accessory_NormalResist_1', 'x'))
      .toBe('Neutralschutzring');
  });
});

describe('matchesQuery', () => {
  it('matches the ENGLISH name when the UI is localised', () => {
    // THE BUG THIS EXISTS TO PREVENT. Someone typing "Melpaca" into a German
    // dashboard must still find the Pal on screen.
    expect(matchesQuery('melpaca', 'Melpaca', 'Melpaca', 'Alpaca')).toBe(true);
    expect(matchesQuery('lamball', 'Lamball', 'Wollipop', 'SheepBall')).toBe(true);
  });

  it('matches the LOCALISED name too', () => {
    expect(matchesQuery('wollipop', 'Lamball', 'Wollipop', 'SheepBall')).toBe(true);
  });

  it('matches the id, because that is what the API speaks', () => {
    expect(matchesQuery('sheepball', 'Lamball', 'Wollipop', 'SheepBall')).toBe(true);
  });

  it('is false only when no name matches', () => {
    expect(matchesQuery('anubis', 'Lamball', 'Wollipop', 'SheepBall')).toBe(false);
  });

  it('an empty query matches everything rather than nothing', () => {
    expect(matchesQuery('   ', 'Lamball', 'Wollipop', 'SheepBall')).toBe(true);
  });
});

describe('the default', () => {
  it('is English, which is not a pack', () => {
    expect(DEFAULT_LANG).toBe('en');
  });
});
