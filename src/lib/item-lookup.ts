import type { CatalogueItem } from './types';

/**
 * Turning what somebody typed into an item id.
 *
 * **This is one rule because there were two, and they had different bugs.**
 * `slot-editor.tsx` and `item-creator.tsx` both mapped free text onto the
 * catalogue, and both got the ambiguous case wrong in ways that looked nothing
 * alike:
 *
 * - the creator tested `id === q || name === q` in a single pass over a list
 *   sorted by id, so an early entry matching on NAME beat a later one matching
 *   on ID — typing "Gunpowder" resolved to the dead `Gunpowder`
 * - the editor built a name map with a bare `set`, i.e. last-wins, which
 *   happened to pick the live `Gunpowder2` and the dead `Head001_5`
 *
 * Neither was reasoned about; both were arbitrary. The same lesson AGENTS.md
 * records for `_scope_pals` — two copies of a filter drift, so the rule moves
 * and the callers pass their own data to it.
 *
 * ## Why a display name is not unique
 *
 * 95 of the game's 2,466 items carry `bLegalInGame: false` **and** share their
 * display name with exactly one legal item. Most of those collide because the
 * game never named the dead one at all and our own exact-first/base-fallback
 * naming rule fills it in from the base id — `Head001_2` shows "Monarch's
 * Crown" because `Head001` is called that. An illegal item wearing a live
 * item's name is precisely a legacy tier, which is what makes the collision
 * useful rather than noise.
 *
 * `liveTwin` is present only on the dead side of such a pair, so it doubles as
 * the tie-break: prefer the entry that does not have one.
 *
 * ## An exact id wins even when it is the dead one, and the UI explains it
 *
 * `Gunpowder` is simultaneously the legacy item's **id** and both items'
 * **display name** — a first draft of the tests here asserted both that typing
 * it resolves to `Gunpowder2` (name rule) and that it stays reachable (id
 * rule), which cannot both hold.
 *
 * It resolves to `Gunpowder`. Literal input is honoured and the badge in
 * `item-creator.tsx` names the live twin, rather than the lookup silently
 * substituting a different item than the one whose id was typed. Substitution
 * would make the legacy ids unreachable for exactly the operator who knows
 * enough to ask for one by id, and would do it invisibly. The ambiguity that
 * *is* resolved silently is the one with no literal reading — a display name,
 * where "Monarch's Crown" has no id to honour and the live `Head001` is the
 * only sane answer.
 */

/** An exact id, matched case-insensitively. Always wins — see `resolveItem`. */
function byExactId(
  catalogue: readonly CatalogueItem[],
  key: string
): CatalogueItem | undefined {
  return catalogue.find((i) => i.id.toLowerCase() === key);
}

/**
 * Resolve typed text to a catalogue entry, or `undefined`.
 *
 * **An exact id always beats a name**, so a legacy id stays deliberately
 * reachable: some saves hold these items, and an editor that could not name
 * what is already in a chest would be worse than one that resolves ambiguously.
 * Only when the text is *not* an id does the name pass run, and there the live
 * item wins.
 *
 * Unresolvable text is not an error here. The caller sends it anyway — the
 * backend validates against all 2,466 items and gives the better message.
 */
export function resolveItem(
  catalogue: readonly CatalogueItem[],
  typed: string
): CatalogueItem | undefined {
  const key = typed.trim().toLowerCase();
  if (!key) return undefined;

  const exact = byExactId(catalogue, key);
  if (exact) return exact;

  const named = catalogue.filter((i) => (i.name ?? '').toLowerCase() === key);
  if (named.length === 0) return undefined;
  // `liveTwin` marks the dead side of a name collision. Absent on the live one,
  // absent on every unambiguous item, so this is a no-op for 2,371 of 2,466.
  return named.find((i) => !i.liveTwin) ?? named[0];
}

/**
 * Whether resolving `typed` silently passed over a same-named legacy id.
 *
 * Used to explain a resolution rather than to change it: somebody who types
 * "Gunpowder" gets `Gunpowder2` and is entitled to know there was another.
 * Returns the ids skipped, so the caller can name them.
 */
export function shadowedByName(
  catalogue: readonly CatalogueItem[],
  typed: string
): string[] {
  const key = typed.trim().toLowerCase();
  if (!key || byExactId(catalogue, key)) return [];
  const named = catalogue.filter((i) => (i.name ?? '').toLowerCase() === key);
  if (named.length < 2) return [];
  const chosen = resolveItem(catalogue, typed);
  return named.filter((i) => i.id !== chosen?.id).map((i) => i.id);
}
