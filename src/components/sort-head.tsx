'use client';

import { ArrowUpDown } from 'lucide-react';

/**
 * A clickable, sortable column header — extracted from `my-pals.tsx`, which
 * grew the pattern first, so the Players roster and the Items catalogue can
 * share it instead of growing three private copies.
 *
 * Generic over the sort-key type so each table keeps its own key union; the
 * click contract is my-pals': first click selects the column, further clicks
 * flip the direction.
 */
export function SortHead<K extends string>({
  label, k, sort, desc, set, flip, align,
}: {
  label: string;
  k: K;
  sort: K;
  desc: boolean;
  set: (k: K) => void;
  flip: (d: boolean) => void;
  align?: 'right';
}) {
  const active = sort === k;
  return (
    <th
      onClick={() => (active ? flip(!desc) : set(k))}
      style={{ cursor: 'pointer', userSelect: 'none', whiteSpace: 'nowrap',
               ...(align === 'right' ? { textAlign: 'right' as const } : {}) }}
      title={active ? (desc ? 'Descending' : 'Ascending') : `Sort by ${label}`}
    >
      {label}
      <ArrowUpDown
        size={10}
        style={{ marginLeft: 3, opacity: active ? 0.9 : 0.25, verticalAlign: '-1px' }}
      />
    </th>
  );
}
