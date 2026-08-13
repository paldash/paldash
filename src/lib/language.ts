/**
 * The game's own display names, in the language the operator chose.
 *
 * ## Why this is a client-side overlay and not a server-side rewrite
 *
 * Localising `name` in the API would mean touching every endpoint that returns
 * one — and it would break search *silently*. The Pal and item boxes are a
 * substring match against the name the client holds, so a German payload stops
 * an English query matching a Pal that is visibly on screen. The ids being
 * canonical and unaffected is the half that makes that look safe, and is not
 * the half that breaks.
 *
 * So the English name stays exactly where it was, the localised one is layered
 * on for display, and `matchesQuery` tests both. A caller that renders
 * `localName(...)` and searches with `matchesQuery(...)` cannot get this wrong.
 */

export type LanguageNames = {
  pals?: Record<string, string>;
  items?: Record<string, string>;
  structures?: Record<string, string>;
};

export type LanguagePack = {
  lang: string;
  names: LanguageNames;
};

/** English is not a pack — it is already the name every payload carries. */
export const DEFAULT_LANG = 'en';

/**
 * The localised name for an id, or `fallback` when there is none.
 *
 * **Ids are matched lowercased**, which is not a tidy-up: the upstream data is
 * inconsistently capitalised (`Sheepball` in a save, `SheepBall` in the tables)
 * and this project resolves everything case-insensitively for that reason.
 *
 * A missing entry is ordinary — a language pack covers the game's own names,
 * and a modded species or a new one after an update simply has none.
 */
export function localName(
  pack: LanguagePack | null,
  section: keyof LanguageNames,
  id: string | null | undefined,
  fallback: string
): string {
  if (!pack || !id) return fallback;
  return pack.names[section]?.[id.toLowerCase()] || fallback;
}

/**
 * Whether a row matches a search query, in **either** language.
 *
 * This exists so the trap is unreachable rather than merely documented: a
 * caller filtering on the localised name alone loses every English query, and
 * one filtering on the English name alone loses every query typed in the
 * language the operator selected. Both are the same bug from opposite ends.
 *
 * The id is included too — it is what the API speaks, and somebody debugging
 * types `SheepBall`.
 */
export function matchesQuery(
  query: string,
  englishName: string | null | undefined,
  localisedName: string | null | undefined,
  id?: string | null
): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return [englishName, localisedName, id].some(
    (candidate) => !!candidate && candidate.toLowerCase().includes(q)
  );
}
