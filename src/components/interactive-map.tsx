'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useDashboardStore } from '@/lib/store';
import { formatCoords, getRegion, MAP_REGIONS, type MapRegion } from '@/lib/map-coordinates';
import { getMapObjects, getFastTravelPoints } from '@/lib/save-api';
import { Layers, Crosshair, RefreshCw, Search, Info } from 'lucide-react';
import dynamic from 'next/dynamic';
import type { MapObject, FastTravelPoint } from '@/lib/types';

const MapComponent = dynamic(() => import('./map-inner'), { ssr: false });

/**
 * Layer toggles.
 *
 * `fastTravel` is the one layer that does not come from the save — fast-travel
 * statues are static level actors, so their positions ship with the dashboard as
 * bundled game data. Everything else is read out of the world.
 */
const LAYERS: { id: string; label: string; color: string; group: 'live' | 'world' | 'base' }[] = [
  { id: 'players', label: 'Players', color: '#5b9dd9', group: 'live' },
  { id: 'bases', label: 'Bases', color: '#c9973f', group: 'live' },

  { id: 'fastTravel', label: 'Fast travel', color: '#e0c060', group: 'world' },
  { id: 'chest', label: 'Chests', color: '#c9973f', group: 'world' },
  { id: 'oreNode', label: 'Ore nodes', color: '#8a8378', group: 'world' },
  { id: 'oilrigChest', label: 'Oil rig', color: '#d97757', group: 'world' },
  { id: 'fishingJunk', label: 'Fishing junk', color: '#5f6b73', group: 'world' },

  { id: 'palbox', label: 'Palboxes', color: '#5b9dd9', group: 'base' },
  { id: 'breeding', label: 'Breeding', color: '#8d84c7', group: 'base' },
  { id: 'statue', label: 'Statues', color: '#4d9e75', group: 'base' },
  { id: 'crafting', label: 'Crafting', color: '#a1a7b0', group: 'base' },
  { id: 'production', label: 'Production', color: '#6d747e', group: 'base' },
  { id: 'farm', label: 'Farms', color: '#7fa05b', group: 'base' },
  { id: 'storage', label: 'Storage', color: '#c25757', group: 'base' },
  { id: 'defense', label: 'Defense', color: '#b0553f', group: 'base' },
];

export default function InteractiveMap() {
  const { onlinePlayers, bases, mapLayers, toggleMapLayer } = useDashboardStore();
  const [mouseCoords, setMouseCoords] = useState<{ x: number; y: number } | null>(null);
  const [mapObjects, setMapObjects] = useState<MapObject[]>([]);
  const [fastTravel, setFastTravel] = useState<FastTravelPoint[]>([]);
  const [region, setRegion] = useState<MapRegion>('palpagos');
  const [query, setQuery] = useState('');
  const [flyTo, setFlyTo] = useState<{ x: number; y: number; nonce: number } | null>(null);
  const [loading, setLoading] = useState(false);
  const flyNonce = useRef(0);

  const load = useCallback(async () => {
    setLoading(true);
    const [objects, points] = await Promise.allSettled([
      getMapObjects(),
      getFastTravelPoints(),
    ]);
    setMapObjects(objects.status === 'fulfilled' ? objects.value : []);
    setFastTravel(points.status === 'fulfilled' ? points.value : []);
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const transform = getRegion(region);

  // Counts are per region, so switching to the World Tree does not claim there
  // are 2,000 chests on it.
  const counts = useMemo(() => {
    const acc: Record<string, number> = {};
    for (const object of mapObjects) {
      if (!transform.contains(object.x, object.y)) continue;
      acc[object.category] = (acc[object.category] ?? 0) + 1;
    }
    acc.fastTravel = fastTravel.filter((p) => transform.contains(p.x, p.y)).length;
    acc.players = onlinePlayers.filter((p) =>
      transform.contains(p.location_x, p.location_y)
    ).length;
    acc.bases = bases.filter((b) => transform.contains(b.x, b.y)).length;
    return acc;
  }, [mapObjects, fastTravel, onlinePlayers, bases, transform]);

  // Search across fast-travel names and base/guild names.
  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (q.length < 2) return [];
    const hits: { label: string; sub: string; x: number; y: number }[] = [];

    for (const point of fastTravel) {
      if (point.name.toLowerCase().includes(q)) {
        hits.push({ label: point.name, sub: 'Fast travel', x: point.x, y: point.y });
      }
    }
    for (const base of bases) {
      if (base.guildName.toLowerCase().includes(q)) {
        hits.push({ label: base.guildName, sub: 'Base', x: base.x, y: base.y });
      }
    }
    return hits.slice(0, 8);
  }, [query, fastTravel, bases]);

  const jump = (x: number, y: number) => {
    // Switch region automatically if the target is on the other landmass.
    const target = MAP_REGIONS.find((r) => r.contains(x, y));
    if (target && target.id !== region) setRegion(target.id);
    // A counter, not Date.now(): it re-triggers the fly-to effect even when the
    // same result is clicked twice, and cannot collide within one millisecond.
    flyNonce.current += 1;
    setFlyTo({ x, y, nonce: flyNonce.current });
    setQuery('');
  };

  const renderGroup = (group: 'live' | 'world' | 'base') =>
    LAYERS.filter((l) => l.group === group).map((layer) => {
      const active = mapLayers[layer.id];
      const count = counts[layer.id] ?? 0;
      return (
        <button
          key={layer.id}
          className="btn"
          style={{
            padding: '3px 9px',
            fontSize: 11,
            background: active ? 'var(--bg-card-hover)' : 'transparent',
            color: active ? 'var(--text-primary)' : 'var(--text-muted)',
            borderColor: active ? layer.color : 'var(--border-primary)',
            opacity: count ? 1 : 0.45,
          }}
          onClick={() => toggleMapLayer(layer.id)}
          title={`${count} ${layer.label.toLowerCase()} on ${transform.label}`}
        >
          <span
            style={{
              width: 7,
              height: 7,
              borderRadius: '50%',
              background: active ? layer.color : 'var(--text-muted)',
              display: 'inline-block',
            }}
          />
          {layer.label}
          <span className="mono" style={{ opacity: 0.6 }}>{count}</span>
        </button>
      );
    });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {/* Region + search */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', gap: 2 }}>
          {MAP_REGIONS.map((r) => (
            <button
              key={r.id}
              className="btn"
              style={{
                padding: '4px 12px',
                fontSize: 12,
                background: region === r.id ? 'var(--bg-card-hover)' : 'transparent',
                color: region === r.id ? 'var(--text-primary)' : 'var(--text-muted)',
              }}
              onClick={() => setRegion(r.id)}
            >
              {r.label}
            </button>
          ))}
        </div>

        <div style={{ position: 'relative', flex: 1, minWidth: 200, maxWidth: 340 }}>
          <Search
            size={13}
            style={{ position: 'absolute', left: 10, top: 9, color: 'var(--text-muted)' }}
          />
          <input
            className="input"
            style={{ paddingLeft: 30 }}
            placeholder="Find a fast travel point or base…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          {results.length > 0 && (
            <div
              style={{
                position: 'absolute',
                top: '100%',
                left: 0,
                right: 0,
                zIndex: 1000,
                marginTop: 4,
                background: 'var(--bg-card)',
                border: '1px solid var(--border-primary)',
                borderRadius: 'var(--radius)',
                overflow: 'hidden',
              }}
            >
              {results.map((hit, i) => (
                <button
                  key={`${hit.label}-${i}`}
                  onClick={() => jump(hit.x, hit.y)}
                  style={{
                    display: 'block',
                    width: '100%',
                    textAlign: 'left',
                    padding: '7px 10px',
                    background: 'transparent',
                    border: 'none',
                    cursor: 'pointer',
                    color: 'var(--text-primary)',
                    fontSize: 12,
                  }}
                >
                  {hit.label}
                  <span style={{ color: 'var(--text-muted)', fontSize: 11, marginLeft: 6 }}>
                    {hit.sub} · {formatCoords(hit.x, hit.y)}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>

        <button
          className="btn btn-ghost"
          style={{ padding: '3px 9px', fontSize: 11, marginLeft: 'auto' }}
          onClick={load}
          disabled={loading}
        >
          <RefreshCw size={11} /> {loading ? 'Loading…' : 'Reload'}
        </button>

        {mouseCoords && (
          <span
            className="mono"
            style={{
              fontSize: 11,
              color: 'var(--text-muted)',
              background: 'var(--bg-card)',
              padding: '3px 9px',
              borderRadius: 'var(--radius)',
              border: '1px solid var(--border-primary)',
            }}
          >
            <Crosshair
              size={10}
              style={{ display: 'inline', verticalAlign: '-1px', marginRight: 4 }}
            />
            {formatCoords(mouseCoords.x, mouseCoords.y)}
          </span>
        )}
      </div>

      {/* Layer toggles, grouped */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexWrap: 'wrap' }}>
        <Layers size={14} style={{ color: 'var(--text-muted)', marginRight: 2 }} />
        {renderGroup('live')}
        <span style={{ width: 1, height: 16, background: 'var(--border-primary)', margin: '0 4px' }} />
        {renderGroup('world')}
        <span style={{ width: 1, height: 16, background: 'var(--border-primary)', margin: '0 4px' }} />
        {renderGroup('base')}
      </div>

      {!transform.calibrated && (
        <div className="notice notice-warn" style={{ fontSize: 12 }}>
          <Info size={13} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 5 }} />
          {transform.note}
        </div>
      )}

      {!mapObjects.length && !loading && (
        <div className="notice" style={{ fontSize: 12 }}>
          No map objects loaded. Save data is parsed on demand — press{' '}
          <strong>Refresh</strong> on the Overview tab to parse the world, then
          reload here.
        </div>
      )}

      <div className="map-container" style={{ height: 'calc(100vh - 250px)', minHeight: 480 }}>
        <MapComponent
          players={onlinePlayers}
          bases={bases}
          mapObjects={mapObjects}
          fastTravel={fastTravel}
          layers={mapLayers}
          region={region}
          flyTo={flyTo}
          onMouseMove={(x, y) => setMouseCoords({ x, y })}
        />
      </div>

      <p style={{ fontSize: 11, color: 'var(--text-muted)' }}>
        Coordinates match the in-game map. Fast-travel points come from bundled
        game data; everything else is read from your world.
      </p>
    </div>
  );
}
