import type { EditField } from './types';

/** What a draft field can hold. `null` is a request, not an absence — see below. */
export type FieldValue = string | number | boolean | string[] | null;

/**
 * Turn an editor draft into the change set the backend takes.
 *
 * Extracted from the component because one of its rules is invisible in the
 * rendered UI and silently destructive if it drifts:
 *
 * **A `clear` field is omitted unless it reads `null`.** Its draft value seeds
 * to the affliction's *name* (`"Fracture"`), which the backend rejects outright
 * — `null` is the only value it accepts, and it means "cure this". So sending
 * the seed fails every preview, and sending `null` unconditionally would cure
 * an affliction nobody asked about, on a Pal the operator opened to rename.
 * Neither failure is visible from reading the form.
 *
 * Everything else is coerced to the kind the schema declares, because an
 * `<input type="number">` yields a string and `"80"` is not `80` to a validator
 * that checks `isinstance(value, int)`.
 */
export function buildChanges(
  draft: Record<string, FieldValue>,
  editable: EditField[]
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(draft)) {
    const field = editable.find((f) => f.name === key);
    if (!field) continue;
    switch (field.kind) {
      case 'clear':
        if (value === null) out[key] = null;
        break;
      case 'int':
      case 'float':
        out[key] = Number(value);
        break;
      case 'bool':
        out[key] = Boolean(value);
        break;
      case 'list':
        out[key] = Array.isArray(value) ? value : [];
        break;
      default:
        out[key] = value;
    }
  }
  return out;
}
