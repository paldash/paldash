/**
 * The tooltip layer over `/api/world/passives/effects`.
 *
 * Pins the two things that would silently mislead: an effect that targets the
 * PLAYER rendered as though it buffed the Pal, and a negative value losing its
 * sign.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { loadPassives, getPassive, describePassive, formatEffect } from './passives';

function effect(over: Partial<Record<string, unknown>> = {}) {
  return {
    type: 'ShotAttack', label: 'attack', value: 20, unit: 'percent',
    category: 'combat', categoryLabel: 'Combat',
    affects: 'pal', affectsLabel: 'this Pal', ...over,
  };
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: true,
    json: async () => ({
      skills: [
        {
          id: 'Legend', name: 'Legend', description: 'Attack +20% Defense +20%',
          rank: 4, invoke: ['InvokeAlways'], whenLabel: 'always',
          effects: [effect(), effect({ type: 'Defense', label: 'Defense' })],
        },
        {
          id: 'Noukin', name: 'Musclehead', description: 'Attack +30% Work Speed -50%',
          rank: 3, invoke: ['InvokeAlways'], whenLabel: 'always',
          effects: [
            effect({ value: 30 }),
            effect({ type: 'CraftSpeed', label: 'work speed', value: -50 }),
          ],
        },
        {
          id: 'TrainerBuff', name: 'Trainer Buff', description: '',
          rank: 1, invoke: ['InvokeInOtomo'], whenLabel: 'while in your party',
          effects: [effect({ affects: 'player', affectsLabel: 'you' })],
        },
      ],
      unknownIds: ['Modded_Nonsense'],
    }),
  })));
});

describe('formatEffect', () => {
  it('always shows the sign, because the negative ones are the interesting ones', () => {
    expect(formatEffect(effect() as never)).toBe('+20% attack');
    expect(formatEffect(effect({ value: -50, label: 'work speed' }) as never))
      .toBe('-50% work speed');
  });

  it('does not put a percent sign on a flat value', () => {
    expect(formatEffect(effect({ unit: 'flat', value: 1, label: 'jump count' }) as never))
      .toBe('+1 jump count');
  });
});

describe('describePassive', () => {
  it('lists the effects under the name', async () => {
    await loadPassives(['Legend']);
    const text = describePassive('Legend');
    expect(text).toContain('Legend');
    expect(text).toContain('+20% attack');
  });

  it('SAYS WHEN AN EFFECT BUFFS YOU RATHER THAN THE PAL', async () => {
    // 669 of the bundle's 2,057 effects target the player. Rendering "+20%
    // attack" on a Pal row without saying whose is the misreading this exists
    // to prevent.
    await loadPassives(['TrainerBuff']);
    expect(describePassive('TrainerBuff')).toContain('(you)');
  });

  it('notes a non-always trigger', async () => {
    await loadPassives(['TrainerBuff']);
    expect(describePassive('TrainerBuff')).toContain('while in your party');
  });

  it('keeps a negative effect visible', async () => {
    await loadPassives(['Noukin']);
    expect(describePassive('Noukin')).toContain('-50% work speed');
  });

  it('falls back to the raw id for something it never learned', () => {
    expect(describePassive('NeverFetched')).toBe('NeverFetched');
  });
});

describe('loadPassives', () => {
  it('does not re-request an id it already has', async () => {
    await loadPassives(['Legend']);
    const calls = (fetch as unknown as { mock: { calls: unknown[] } }).mock.calls.length;
    await loadPassives(['Legend']);
    expect((fetch as unknown as { mock: { calls: unknown[] } }).mock.calls.length).toBe(calls);
    expect(getPassive('Legend')?.name).toBe('Legend');
  });

  it('stops asking for an id the backend does not know', async () => {
    await loadPassives(['Modded_Nonsense']);
    const calls = (fetch as unknown as { mock: { calls: unknown[] } }).mock.calls.length;
    await loadPassives(['Modded_Nonsense']);
    expect((fetch as unknown as { mock: { calls: unknown[] } }).mock.calls.length).toBe(calls);
  });

  it('a failed fetch costs tooltips, not the table', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('offline'); }));
    await expect(loadPassives(['SomethingNew'])).resolves.toBeUndefined();
    expect(describePassive('SomethingNew')).toBe('SomethingNew');
  });
});
