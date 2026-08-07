import { asArray } from './arrays';
/**
 * The game's own work-suitability list: id, display name, icon, and **order**.
 *
 * All four come from `work_suitability.json` in the reference archive and are
 * already in `gamedata.json.gz` — this only fetches and caches them. The pieces
 * that were missing were the *icons* (never installed until now) and any client
 * access at all.
 *
 * WHY THE ORDER MATTERS
 * ---------------------
 * `index` is the game's own ordering — Kindling, Watering, Planting, Electricity,
 * Handiwork, … — which is the order every player already reads on a Pal's page
 * in game. Sorting alphabetically, or by whatever order a `Record` happens to
 * enumerate, means the dashboard lists the same thirteen things in a different
 * sequence from the game and nobody can scan it. That is the whole reason this
 * is fetched rather than hardcoded.
 *
 * FALLBACK
 * --------
 * A hardcoded copy backs it up, because the Paldeck and the My Pals filter must
 * work on a clone with no bundled game data. It is deliberately the same 13
 * entries in the same order; if the game ever adds a work type, the fetched list
 * carries it and the fallback simply lags.
 */

export interface WorkType {
  id: string;
  label: string;
  icon: string;
  index: number;
}

/** Same ids and order as the bundled table. Used only when the fetch fails. */
const FALLBACK: WorkType[] = [
  ['EmitFlame', 'Kindling'],
  ['Watering', 'Watering'],
  ['Seeding', 'Planting'],
  ['GenerateElectricity', 'Generating Electricity'],
  ['Handcraft', 'Handiwork'],
  ['Collection', 'Gathering'],
  ['Deforest', 'Lumbering'],
  ['Mining', 'Mining'],
  ['Transport', 'Transporting'],
  ['MonsterFarm', 'Ranching'],
  ['ProductMedicine', 'Medicine Production'],
  ['OilExtraction', 'Oil Extraction'],
  ['Cool', 'Cooling'],
].map(([id, label], index) => ({ id, label, icon: '', index }));

let cache: WorkType[] | null = null;
let inFlight: Promise<WorkType[]> | null = null;

/**
 * The work types, fetched once per page load.
 *
 * Deduplicated through `inFlight` because several components ask on mount and
 * this is bundled reference data that cannot change while the page is open —
 * three simultaneous mounts should cost one request, not three.
 */
export async function getWorkTypes(): Promise<WorkType[]> {
  if (cache) return cache;
  if (inFlight) return inFlight;

  inFlight = fetch('/api/save/world/reference')
    .then((res) => (res.ok ? res.json() : Promise.reject(new Error(String(res.status)))))
    .then((body) => {
      // **`?? []` WAS THE BUG.** It substitutes only for null/undefined, so a
      // `workSuitability` that arrived as an object went straight through and
      // threw `.map is not a function` — which killed the whole My Pals tab,
      // because this module is imported by it and the throw escaped the render.
      const raw = asArray<{
        id: string; display_name?: string; icon?: string; index?: number;
      }>(body?.workSuitability as never, 'workSuitability');
      const mapped = raw
        .map((w, i) => ({
          id: w.id,
          label: w.display_name || w.id,
          icon: w.icon || '',
          index: w.index ?? i,
        }))
        .sort((a, b) => a.index - b.index);
      cache = mapped.length ? mapped : FALLBACK;
      return cache;
    })
    .catch(() => {
      cache = FALLBACK;
      return cache;
    })
    .finally(() => {
      inFlight = null;
    });

  return inFlight;
}

/**
 * A Pal's work levels, in the game's order, highest first is NOT applied.
 *
 * Game order deliberately, not "best first": a player scanning several Pals
 * compares the same slot in the same place each time, which sorting per-Pal
 * destroys. Sorting by *strength* is a thing the table's column sort does, on
 * one chosen work type, across Pals — which is the comparison that actually
 * answers "who should mine".
 */
export function orderedWork(
  levels: Record<string, number> | undefined,
  types: WorkType[]
): { type: WorkType; level: number }[] {
  if (!levels) return [];
  return types
    .map((type) => ({ type, level: levels[type.id] ?? 0 }))
    .filter((entry) => entry.level > 0);
}
