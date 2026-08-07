/**
 * `?? []` took out a live tab. These pin why the replacement is different.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { asArray } from './arrays';

let warn: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
});

describe('asArray', () => {
  it('passes a real array through unchanged', () => {
    const input = [1, 2, 3];
    expect(asArray(input)).toBe(input);
  });

  it('returns [] for null and undefined, exactly as `?? []` did', () => {
    expect(asArray(null)).toEqual([]);
    expect(asArray(undefined)).toEqual([]);
  });

  it('RETURNS [] FOR AN OBJECT — the case `?? []` let through', () => {
    // This is the whole bug. `(x ?? []).map` on an object throws "map is not a
    // function", and that throw escaped a render and killed the My Pals tab
    // rather than degrading to an empty list.
    expect(asArray({ a: 1 } as never, 'obj-case')).toEqual([]);
    expect(asArray('nope' as never, 'str-case')).toEqual([]);
    expect(asArray(42 as never, 'num-case')).toEqual([]);
  });

  it('does not warn for a legitimately absent field', () => {
    warn.mockClear();
    asArray(undefined, 'absent-field');
    asArray(null, 'absent-field-2');
    expect(warn).not.toHaveBeenCalled();
  });

  it('warns once per field, not once per render', () => {
    // The dedupe is module-level and deliberate: a broken field in a table body
    // would otherwise log once per row per render and bury itself. Each test
    // therefore uses a field name no other test has used.
    warn.mockClear();
    for (let i = 0; i < 100; i++) asArray({} as never, 'noisy-field');
    expect(warn).toHaveBeenCalledTimes(1);
  });
});
