import { describe, it, expect } from 'vitest';
import { buildChanges } from './edit-changes';
import type { EditField } from './types';

function field(name: string, kind: EditField['kind']): EditField {
  return { name, kind, label: name, min: null, max: null, choices: null, note: '' };
}

const FIELDS = [
  field('nickname', 'string'),
  field('level', 'int'),
  field('sanity', 'float'),
  field('isImported', 'bool'),
  field('workerSick', 'clear'),
  field('masteredSkills', 'list'),
];

describe('buildChanges', () => {
  it('omits an untouched clear field rather than sending its current value', () => {
    // The seed is the affliction's name, which the backend rejects outright.
    // Sending it would fail every preview on a Pal that happens to be ill.
    const changes = buildChanges({ workerSick: 'Fracture', level: 10 }, FIELDS);
    expect(changes).not.toHaveProperty('workerSick');
    expect(changes.level).toBe(10);
  });

  it('sends null once the clear field has been asked to clear', () => {
    const changes = buildChanges({ workerSick: null }, FIELDS);
    expect(changes.workerSick).toBeNull();
  });

  it('never sends a clear field for a Pal that has no affliction to cure', () => {
    // A healthy Pal carries no `WorkerSick` property, so the field is not in
    // the draft at all — and an unconditional `null` here would be a silent
    // write request against a property that does not exist.
    const changes = buildChanges({ nickname: 'Woolly' }, FIELDS);
    expect(Object.keys(changes)).toEqual(['nickname']);
  });

  it('coerces number inputs, which hand back strings', () => {
    const changes = buildChanges({ level: '42', sanity: '87.5' }, FIELDS);
    expect(changes.level).toBe(42);
    expect(changes.sanity).toBe(87.5);
  });

  it('keeps a float a float rather than rounding it to the int path', () => {
    expect(buildChanges({ sanity: 87.5 }, FIELDS).sanity).toBe(87.5);
  });

  it('coerces bool, so an unchecked box is false rather than absent', () => {
    expect(buildChanges({ isImported: false }, FIELDS).isImported).toBe(false);
  });

  it('drops fields the schema does not declare', () => {
    // The draft is seeded from a Pal record and carries more than the schema
    // allows; anything unlisted must not reach a writer.
    expect(buildChanges({ speciesId: 'Sheepball' }, FIELDS)).toEqual({});
  });

  it('substitutes an empty array for a list that is not one', () => {
    expect(buildChanges({ masteredSkills: 'oops' }, FIELDS).masteredSkills).toEqual([]);
  });
});
