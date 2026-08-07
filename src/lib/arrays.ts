/**
 * `asArray` — because `?? []` does not do what every call site assumed.
 *
 * A live server hit **"((intermediate value) ?? []).map is not a function"** and
 * lost the whole My Pals tab. The pattern behind it was everywhere:
 *
 *     const raw = (body?.workSuitability ?? []) as Thing[];
 *     raw.map(...)
 *
 * `??` substitutes only for `null` and `undefined`. **An object, a string or a
 * number sails straight through it** and then throws on `.map`, so the guard
 * that looks like it is protecting the render is protecting nothing — it covers
 * the one case that would not have crashed anyway (`undefined.map` is a
 * TypeError too, but a *nullish* field is also the case nobody sees in practice)
 * and misses every shape surprise, which is the case that actually happens.
 *
 * The shape surprise is not hypothetical here. These payloads come from a
 * backend whose bundles are regenerated from game files, across a proxy, into a
 * container image that is updated independently of the browser tab holding the
 * page. A field that is a list today can be an object after an upgrade, and the
 * failure mode should be an empty list, never a dead tab.
 *
 * **This is the `.catch(() => [])` lesson pointed the other way.** AGENTS.md
 * records that swallowing a fetch error into `[]` destroys the difference
 * between "nothing" and "we could not ask". That argument is about *errors*.
 * This is about *shape*: a value that is not a list cannot be rendered as one,
 * and there is no information to preserve by crashing — the caller already
 * cannot show it. So the right move is to degrade, and to say so loudly enough
 * that a developer notices.
 */

/**
 * The value if it really is an array, otherwise `[]`.
 *
 * Warns once per unique payload shape rather than per render — a broken field
 * in a table body would otherwise log thousands of times and bury itself.
 */
export function asArray<T>(
  // Typed as an array because that is what the API type CLAIMS. The runtime
  // check is here precisely because that claim comes from a server which may be
  // on a different version than the page holding this type.
  value: readonly T[] | null | undefined,
  what = 'value'
): T[] {
  if (Array.isArray(value)) return value as T[];
  if (value !== null && value !== undefined) warnOnce(what, value);
  return [];
}

const warned = new Set<string>();

function warnOnce(what: string, value: unknown) {
  if (warned.has(what)) return;
  warned.add(what);
  // Deliberately a warning and not a throw. Rendering nothing is recoverable;
  // taking out the tab is what this exists to stop.
  console.warn(
    `[dashboard] expected ${what} to be an array, got ${typeof value}. ` +
      `Rendering it as empty. This usually means the backend and the page are ` +
      `on different versions — reload after a container rebuild.`
  );
}
