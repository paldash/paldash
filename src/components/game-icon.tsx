'use client';

import { useState } from 'react';
import { hasIcon } from '@/lib/icons';

/**
 * A game icon, or reserved blank space.
 *
 * Takes the **path the API already returned** (`item.icon`, `pal.icon`) rather
 * than an id to look up — `gamedata.json.gz` records it and the installer keeps
 * the archive's filenames, so there is nothing to resolve. See `lib/icons.ts`
 * for why the lookup-table version was wrong.
 *
 * Missing icons reserve their space instead of collapsing, for two reasons: a
 * clone that skipped `install-icons.py` has none at all and must not reflow every
 * table, and rows must not shift when one entry has artwork and its neighbour
 * does not.
 *
 * `onError` covers the third case — an icon the data references but the
 * installed category does not include (93.4% of Pals resolve; boss and variant
 * forms mostly do not). A broken-image glyph is worse than a gap.
 */
export default function GameIcon({
  src,
  size = 22,
  title,
}: {
  src: string | null | undefined;
  size?: number;
  title?: string;
}) {
  const [failed, setFailed] = useState(false);

  if (!hasIcon(src) || failed) {
    return <span style={{ display: 'inline-block', width: size, height: size, flexShrink: 0 }} />;
  }

  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt=""
      title={title}
      width={size}
      height={size}
      loading="lazy"
      onError={() => setFailed(true)}
      style={{ flexShrink: 0, verticalAlign: 'middle' }}
    />
  );
}
