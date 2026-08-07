/**
 * `x.toLocaleString()` on an absent field took out the My Pals tab, right after
 * the array guards fixed the previous throw in the same component.
 */
import { describe, it, expect } from 'vitest';
import { num, fixed, count } from './format';

describe('num', () => {
  it('formats a real number', () => {
    expect(num(1234)).toBe((1234).toLocaleString());
    expect(num(0)).toBe('0');
  });

  it('RETURNS THE DASH FOR UNDEFINED — the case that threw', () => {
    expect(num(undefined)).toBe('—');
    expect(num(null)).toBe('—');
  });

  it('does not fall back to zero, because a zero is a measurement', () => {
    // "0 Pals scanned" is a claim about the save. "—" is a claim about us.
    expect(num(undefined)).not.toBe('0');
  });

  it('rejects NaN and Infinity, which format as garbage rather than throwing', () => {
    expect(num(NaN)).toBe('—');
    expect(num(Infinity)).toBe('—');
  });

  it('rejects a numeric string rather than coercing', () => {
    // Coercing would hide a real API shape change behind a plausible number.
    expect(num('42' as never)).toBe('—');
  });
});

describe('count', () => {
  it('counts an array', () => {
    expect(count([1, 2, 3])).toBe('3');
    expect(count([])).toBe('0');
  });

  it('handles the two ways `report.pals.length` throws', () => {
    expect(count(undefined)).toBe('—');
    expect(count({ a: 1 } as never)).toBe('—');
  });
});

describe('fixed', () => {
  it('formats and degrades', () => {
    expect(fixed(1.234)).toBe('1.23');
    expect(fixed(undefined)).toBe('—');
  });
});
