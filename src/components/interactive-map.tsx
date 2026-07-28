'use client';

import { useCallback, useEffect, useState } from 'react';
import { useDashboardStore } from '@/lib/store';
import { mapToWorld, formatCoords } from '@/lib/map-coordinates';
import { getMapObjects } from '@/lib/save-api';
import { Layers, Crosshair, RefreshCw } from 'lucide-react';
import dynamic from 'next/dynamic';
import type { MapObject } from '@/lib/types';

const MapComponent = dynamic(() => import('./map-inner'), { ssr: false });

/**
 * Layers are driven by what the save actually contains. Fast-travel statues and
 * tower bosses are static world data that does not live in save files, so they
 * are not offered here rather than shown as empty toggles.
 */
const LAYERS: { id: string; label: string; color: string }[] = [
  { id: 'players', label: 'Players', color: '#5b9dd9' },
  { id: 'bases', label: 'Bases', color: '#c9973f' },
  { id: 'chest', label: 'Chests', color: '#c9973f' },
  { id: 'palbox', label: 'Palboxes', color: '#5b9dd9' },
  { id: 'breeding', label: 'Breeding', color: '#8d84c7' },
  { id: 'statue', label: 'Statues', color: '#4d9e75' },
  { id: 'crafting', label: 'Crafting', color: '#a1a7b0' },
  { id: 'production', label: 'Production', color: '#6d747e' },
  { id: 'storage', label: 'Storage', color: '#c25757' },
];

export default function InteractiveMap() {
  const { onlinePlayers, bases, mapLayers, toggleMapLayer } = useDashboardStore();
  const [mouseCoords, setMouseCoords] = useState<{ x: number; y: number } | null>(null);
  const [mapObjects, setMapObjects] = useState<MapObject[]>([]);
  const [loading, setLoading] = useState(false);

  const loadObjects = useCallback(async () => {
    setLoading(true);
    try {
      setMapObjects(await getMapObjects());
    } catch {
      setMapObjects([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadObjects();
  }, [loadObjects]);

  const counts = mapObjects.reduce<Record<string, number>>((acc, object) => {
    acc[object.category] = (acc[object.category] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexWrap: 'wrap' }}>
        <Layers size={14} style={{ color: 'var(--text-muted)', marginRight: 4 }} />
        {LAYERS.map((layer) => {
          const active = mapLayers[layer.id];
          const count = layer.id === 'players' ? onlinePlayers.length
            : layer.id === 'bases' ? bases.length
            : counts[layer.id] ?? 0;
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
              }}
              onClick={() => toggleMapLayer(layer.id)}
              title={`${count} ${layer.label.toLowerCase()}`}
            >
              <span
                style={{
                  width: 7, height: 7, borderRadius: '50%',
                  background: active ? layer.color : 'var(--text-muted)',
                  display: 'inline-block',
                }}
              />
              {layer.label}
              <span className="mono" style={{ opacity: 0.6 }}>{count}</span>
            </button>
          );
        })}

        <button
          className="btn btn-ghost"
          style={{ padding: '3px 9px', fontSize: 11, marginLeft: 'auto' }}
          onClick={loadObjects}
          disabled={loading}
        >
          <RefreshCw size={11} /> {loading ? 'Loading…' : 'Reload'}
        </button>

        {mouseCoords && (
          <span
            className="mono"
            style={{
              fontSize: 11, color: 'var(--text-muted)',
              background: 'var(--bg-card)', padding: '3px 9px',
              borderRadius: 'var(--radius)', border: '1px solid var(--border-primary)',
            }}
          >
            <Crosshair size={10} style={{ display: 'inline', verticalAlign: '-1px', marginRight: 4 }} />
            {formatCoords(mouseCoords.x, mouseCoords.y)}
          </span>
        )}
      </div>

      {!mapObjects.length && !loading && (
        <div className="notice" style={{ fontSize: 12 }}>
          No map objects loaded. Save data is parsed on demand — press{' '}
          <strong>Refresh</strong> on the Overview tab to parse the world, then
          reload here.
        </div>
      )}

      <div className="map-container" style={{ height: 'calc(100vh - 210px)', minHeight: 500 }}>
        <MapComponent
          players={onlinePlayers}
          bases={bases}
          mapObjects={mapObjects}
          layers={mapLayers}
          onMouseMove={(lat: number, lng: number) => setMouseCoords(mapToWorld(lat, lng))}
        />
      </div>

      <p style={{ fontSize: 11, color: 'var(--text-muted)' }}>
        Coordinates match the in-game map. Drop a Palworld map image at{' '}
        <span className="mono">public/palworld-map.png</span> (4096×4096) to
        replace the grid background.
      </p>
    </div>
  );
}
