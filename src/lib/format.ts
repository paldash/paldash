/**
 * `num` — because `x.toLocaleString()` throws when `x` is absent.
 *
 * Second in the family after `asArray`, and from the same live incident: once
 * the array guards landed, the My Pals tab moved on to **"Cannot read
 * properties of undefined (reading 'toLocaleString')"**.
 *
 * The shape is identical to the `?? []` problem. A payload field is typed as a
 * `number` because that is what the API type claims, the component calls
 * `.toLocaleString()` on it directly, and the field is absent — because the
 * backend is a container rebuild ahead of or behind the page, because a Pal is
 * one of the 99 NPCs with no stat scaling, or because an endpoint answered with
 * an error object. TypeScript cannot see any of that; it is checking a promise
 * about a different process.
 *
 * A missing number renders as an em dash, which is what every other "we do not
 * have this" in the dashboard already looks like. It does **not** render as
 * `0` — a zero is a measurement and would be a lie about the save.
 */

/** A formatted number, or `fallback` when it is not actually a number. */
export function num(value: unknown, fallback = '—'): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? value.toLocaleString()
    : fallback;
}

/** Fixed-decimal form, same contract. */
export function fixed(value: unknown, digits = 2, fallback = '—'): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? value.toFixed(digits)
    : fallback;
}

/**
 * `.length` of something that may not be an array.
 *
 * `report.pals.length.toLocaleString()` throws twice over — once if `pals` is
 * undefined, once if it is an object with no `length`.
 */
export function count(value: unknown, fallback = '—'): string {
  return Array.isArray(value) ? value.length.toLocaleString() : fallback;
}
