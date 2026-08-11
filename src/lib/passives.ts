/**
 * What a Pal's passives actually do, fetched once for the whole table.
 *
 * `/api/world/passives/effects` is catalogue data — it describes the game, not
 * this world — so the answer for `Legend` is the same for every Pal that has it
 * and for every user who asks. A table of 900 Pals references perhaps 200
 * distinct passives, so this fetches the distinct set once rather than per row.
 *
 * **Batched at 64 because the route refuses more**, which is deliberate on its
 * side: an unbounded id list is a way to make one request do arbitrary work.
 *
 * The cache is module-level and never invalidated. That is correct here and
 * would not be for world data: bundled reference data cannot change while the
 * page is open, and `getWorkTypes` takes the same approach for the same reason.
 */

import { asArray } from './arrays';

export interface PassiveEffect {
  type: string;
  label: string;
  value: number;
  unit: 'percent' | 'flat';
  category: string | null;
  categoryLabel: string;
  affects: string;
  affectsLabel: string;
}

export interface PassiveSkill {
  id: string;
  name: string;
  description: string;
  rank: number;
  invoke: string[];
  whenLabel: string;
  effects: PassiveEffect[];
}

const cache = new Map<string, PassiveSkill>();
const missing = new Set<string>();
let inFlight: Promise<void> | null = null;

const BATCH = 64;

/**
 * Load descriptions for these passive ids, skipping any already known.
 *
 * Resolves even on failure — a tooltip is not worth breaking a table for, and
 * the caller renders the bare id when a description is absent, which is exactly
 * what it did before this existed.
 */
export async function loadPassives(ids: string[]): Promise<void> {
  const wanted = [...new Set(ids)].filter(
    (id) => id && !cache.has(id) && !missing.has(id)
  );
  if (wanted.length === 0) return;

  // Serialise concurrent callers rather than firing duplicate batches: several
  // components mount at once and would otherwise each request the same set.
  const previous = inFlight ?? Promise.resolve();
  inFlight = previous.then(async () => {
    for (let i = 0; i < wanted.length; i += BATCH) {
      const chunk = wanted.slice(i, i + BATCH);
      try {
        const res = await fetch(
          `/api/save/world/passives/effects?ids=${encodeURIComponent(chunk.join(','))}`
        );
        if (!res.ok) throw new Error(String(res.status));
        const body = await res.json();
        for (const skill of asArray<PassiveSkill>(body?.skills, 'passive skills')) {
          if (skill?.id) cache.set(skill.id, skill);
        }
        // Ids the backend did not know: remember so we stop asking.
        for (const id of asArray<string>(body?.unknownIds, 'unknown passive ids')) {
          missing.add(id);
        }
        for (const id of chunk) if (!cache.has(id)) missing.add(id);
      } catch {
        // Give up on this chunk only. A network blip must not poison the ids
        // permanently, so they are NOT added to `missing` here.
        return;
      }
    }
  });
  await inFlight;
}

export function getPassive(id: string): PassiveSkill | undefined {
  return cache.get(id);
}

/** `+20%` / `-50%` / `+1`. The sign is always shown — a negative passive is
 *  the interesting one, and `Noukin` is -50% craft speed. */
export function formatEffect(effect: PassiveEffect): string {
  const sign = effect.value > 0 ? '+' : '';
  const unit = effect.unit === 'percent' ? '%' : '';
  return `${sign}${effect.value}${unit} ${effect.label}`;
}

/**
 * A plain-text summary for a `title` attribute.
 *
 * Includes who each effect reaches, because 669 of the bundle's 2,057 effects
 * target the PLAYER rather than the Pal — showing "+10% attack" on a Pal row
 * without saying whose attack is the misreading this whole module exists to
 * prevent.
 */
export function describePassive(id: string): string {
  const skill = cache.get(id);
  if (!skill) return id;

  const lines = [skill.name];
  if (skill.description) lines.push(skill.description);
  for (const effect of skill.effects) {
    const who = effect.affects === 'pal' ? '' : ` (${effect.affectsLabel})`;
    lines.push(`  ${formatEffect(effect)}${who}`);
  }
  if (skill.whenLabel && skill.whenLabel !== 'always') {
    lines.push(`  applies ${skill.whenLabel}`);
  }
  return lines.join('\n');
}
