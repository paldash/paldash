import { describe, it, expect } from 'vitest';
import { resolveItem, shadowedByName } from './item-lookup';
import type { CatalogueItem } from './types';

/**
 * The fixtures mirror real catalogue rows, including the two that made the old
 * per-component implementations disagree: `Gunpowder`/`Gunpowder2` (where
 * last-wins happened to be right) and `Head001`/`Head001_5` (where it was
 * wrong). Sorted by id, because that is the order the API returns.
 */
const item = (
  id: string,
  name: string,
  liveTwin?: string
): CatalogueItem => ({
  id,
  name,
  icon: '',
  rarity: 0,
  typeA: '',
  typeB: '',
  maxStack: 1,
  weight: 0,
  hasDurability: false,
  ...(liveTwin ? { legalInGame: false as const, liveTwin } : {}),
});

const CATALOGUE: CatalogueItem[] = [
  item('Gunpowder', 'Gunpowder', 'Gunpowder2'),
  item('Gunpowder2', 'Gunpowder'),
  item('Head001', "Monarch's Crown"),
  item('Head001_2', "Monarch's Crown", 'Head001'),
  item('Head001_5', "Monarch's Crown", 'Head001'),
  item('Leather', 'Leather'),
  item('Leather2', 'Leather Scrap', 'Leather'),
  item('PalSphere', 'Pal Sphere'),
];

describe('resolveItem', () => {
  it('prefers the live item when an ambiguous NAME is not also an id', () => {
    // Last-wins picked `Head001_5`, a legacy tier; the creator's single pass
    // over an id-sorted list would have picked `Head001`. Both were arbitrary —
    // they just failed on different rows. "Monarch's Crown" is nobody's id, so
    // there is no literal reading to honour and the live item is the answer.
    expect(resolveItem(CATALOGUE, "Monarch's Crown")?.id).toBe('Head001');
  });

  it('honours an exact id even when it is the dead one', () => {
    // `Gunpowder` is BOTH the legacy id and both items' display name. It
    // resolves to the legacy item and `item-creator.tsx` badges it with the
    // live twin — silently substituting `Gunpowder2` would make the legacy ids
    // unreachable for the one operator who knew enough to ask by id.
    expect(resolveItem(CATALOGUE, 'Gunpowder')?.id).toBe('Gunpowder');
    expect(resolveItem(CATALOGUE, 'Gunpowder')?.liveTwin).toBe('Gunpowder2');
  });

  it('lets an exact id reach the legacy item on purpose', () => {
    // Not a loophole. These ids exist in real saves, and an editor that cannot
    // name what is already in a chest is worse than an ambiguous one.
    expect(resolveItem(CATALOGUE, 'Head001_5')?.id).toBe('Head001_5');
    expect(resolveItem(CATALOGUE, 'head001_5')?.id).toBe('Head001_5');
  });

  it('matches an id ahead of any name', () => {
    // `Leather2` is the id of "Leather Scrap"; `Leather` is a different item
    // whose NAME is "Leather". An id must never lose to somebody else's name.
    expect(resolveItem(CATALOGUE, 'Leather2')?.id).toBe('Leather2');
    expect(resolveItem(CATALOGUE, 'Leather')?.id).toBe('Leather');
  });

  it('folds case and trims, on both id and name', () => {
    expect(resolveItem(CATALOGUE, '  pal sphere ')?.id).toBe('PalSphere');
    expect(resolveItem(CATALOGUE, 'PALSPHERE')?.id).toBe('PalSphere');
  });

  it('returns undefined rather than guessing', () => {
    expect(resolveItem(CATALOGUE, '')).toBeUndefined();
    expect(resolveItem(CATALOGUE, '   ')).toBeUndefined();
    expect(resolveItem(CATALOGUE, 'Not An Item')).toBeUndefined();
  });

  it('is a no-op for the unambiguous majority', () => {
    for (const entry of CATALOGUE) {
      expect(resolveItem(CATALOGUE, entry.id)?.id).toBe(entry.id);
    }
  });
});

describe('shadowedByName', () => {
  it('names what a name lookup passed over', () => {
    expect(shadowedByName(CATALOGUE, "Monarch's Crown"))
      .toEqual(['Head001_2', 'Head001_5']);
  });

  it('says nothing when an exact id was typed', () => {
    // Typing an id is a decision, not an ambiguity to explain — including
    // `Gunpowder`, which is a legacy id that happens to read as a name. The
    // badge covers that case; this function is only for silent substitutions.
    expect(shadowedByName(CATALOGUE, 'Head001_5')).toEqual([]);
    expect(shadowedByName(CATALOGUE, 'Gunpowder2')).toEqual([]);
    expect(shadowedByName(CATALOGUE, 'Gunpowder')).toEqual([]);
  });

  it('says nothing for an unambiguous or unknown name', () => {
    expect(shadowedByName(CATALOGUE, 'Pal Sphere')).toEqual([]);
    expect(shadowedByName(CATALOGUE, 'Not An Item')).toEqual([]);
    expect(shadowedByName(CATALOGUE, '')).toEqual([]);
  });
});
