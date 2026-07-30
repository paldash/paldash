'use client';

import { useMemo } from 'react';
import { worldToImage, getRegion, MAP_SIZE, type MapRegion } from '@/lib/map-coordinates';
import type { HabitatRegion } from '@/lib/types';

/**
 * A small map shading where one species spawns — the Paldeck's habitat view.
 *
 * Plain SVG over the map texture rather than a second Leaflet instance. There
 * is no panning, zooming or hit-testing to do: it is a thumbnail answering
 * "roughly where do I go", and a Leaflet map per Paldeck entry would be a lot
 * of machinery for a static picture.
 *
 * **Regions are cells, not points.** The extraction resolves habitats to
 * 25,600-unit World Partition cells, which is the resolution the underlying
 * evidence supports — a spawner sheet says a species is referenced by spawners
 * in an area, not that it stands on a particular rock. Overlapping translucent
 * squares therefore read correctly: denser overlap is genuinely more spawners.
 *
 * The two landmasses have separate framings, so a habitat spanning both is
 * split by the caller and rendered as two of these.
 */
export default function HabitatMap({
  regions,
  region,
  size = 260,
}: {
  regions: HabitatRegion[];
  region: MapRegion;
  size?: number;
}) {
  const transform = getRegion(region);
  const scale = size / MAP_SIZE;

  const rects = useMemo(
    () =>
      regions.map((r, i) => {
        // A cell is an axis-aligned box in world space, but the transform swaps
        // and negates axes, so its corners can land in any order in image
        // space. Normalising with min/abs keeps the rectangle valid instead of
        // emitting a negative width that SVG silently drops.
        const a = worldToImage(r.x, r.y, region);
        const b = worldToImage(r.x + r.width, r.y + r.height, region);
        return {
          key: i,
          x: Math.min(a.x, b.x) * scale,
          y: Math.min(a.y, b.y) * scale,
          width: Math.abs(b.x - a.x) * scale,
          height: Math.abs(b.y - a.y) * scale,
        };
      }),
    [regions, region, scale]
  );

  return (
    <div style={{ position: 'relative', width: size, height: size, flexShrink: 0 }}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={transform.image}
        alt=""
        width={size}
        height={size}
        style={{ display: 'block', borderRadius: 6, opacity: 0.75 }}
      />
      <svg
        width={size}
        height={size}
        style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}
      >
        {rects.map((r) => (
          <rect
            key={r.key}
            x={r.x}
            y={r.y}
            width={r.width}
            height={r.height}
            fill="#e5484d"
            fillOpacity={0.38}
          />
        ))}
      </svg>
      {!transform.calibrated && (
        <div
          style={{
            position: 'absolute', bottom: 4, left: 6, right: 6,
            fontSize: 9, color: 'var(--text-muted)', textAlign: 'center',
          }}
        >
          approximate placement
        </div>
      )}
    </div>
  );
}
