'use client';

import { useEffect, useRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { worldToMap, worldToGameMap, MAP_SIZE } from '@/lib/map-coordinates';
import type { Player, BaseCamp, MapObject } from '@/lib/types';

interface Props {
  players: Player[];
  bases: BaseCamp[];
  mapObjects: MapObject[];
  layers: Record<string, boolean>;
  onMouseMove: (lat: number, lng: number) => void;
}

/**
 * Marker styling per point-of-interest category. Kept muted and consistent —
 * the map should read as data, not decoration.
 */
const CATEGORY_STYLE: Record<string, { color: string; size: number; label: string }> = {
  chest:      { color: '#c9973f', size: 6, label: 'Chest' },
  palbox:     { color: '#5b9dd9', size: 8, label: 'Palbox' },
  breeding:   { color: '#8d84c7', size: 7, label: 'Breeding farm' },
  statue:     { color: '#4d9e75', size: 8, label: 'Statue' },
  crafting:   { color: '#a1a7b0', size: 5, label: 'Crafting' },
  production: { color: '#6d747e', size: 5, label: 'Production' },
  storage:    { color: '#c25757', size: 5, label: 'Storage' },
  comfort:    { color: '#6d747e', size: 4, label: 'Comfort' },
  egg:        { color: '#c9973f', size: 6, label: 'Egg' },
};

/**
 * Drawing thousands of individual Leaflet markers janks the browser, so
 * point-of-interest categories render as lightweight circle markers and are
 * capped. Bases and players stay as real markers with popups.
 */
const MAX_POI_MARKERS = 1500;

export default function MapInner({ players, bases, mapObjects, layers, onMouseMove }: Props) {
  const mapRef = useRef<L.Map | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const playerLayer = useRef<L.LayerGroup>(L.layerGroup());
  const baseLayer = useRef<L.LayerGroup>(L.layerGroup());
  const poiLayer = useRef<L.LayerGroup>(L.layerGroup());

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const map = L.map(containerRef.current, {
      crs: L.CRS.Simple,
      minZoom: -3,
      maxZoom: 3,
      zoomControl: true,
      attributionControl: false,
      preferCanvas: true,
    });

    const bounds: L.LatLngBoundsExpression = [[0, 0], [MAP_SIZE, MAP_SIZE]];

    /*
     * Background images.
     *
     * Palworld 1.0 has two landmasses (Palpagos and the Feybreak island) but a
     * single continuous world coordinate space — the calibration samples span
     * both — so markers place correctly regardless of which image is shown.
     *
     * Each entry is tried in order and simply skipped if the file is absent, so
     * you can supply one combined image or one per region. To place a region
     * image precisely, give its world-coordinate extent and we convert it to
     * map bounds; omit `world` to stretch the image across the full map.
     *
     * The previous version drew a procedurally generated fake island, which
     * looked like terrain but corresponded to nothing — markers appeared to sit
     * in the sea. A neutral grid is honest about what we do and don't know.
     */
    const overlays: { src: string; world?: { x1: number; y1: number; x2: number; y2: number } }[] = [
      { src: '/palworld-map.png' },
      { src: '/palworld-map-feybreak.png' },
    ];

    let anyLoaded = false;
    for (const overlay of overlays) {
      const probe = new Image();
      probe.onload = () => {
        anyLoaded = true;
        const target: L.LatLngBoundsExpression = overlay.world
          ? [
              worldToMap(overlay.world.x1, overlay.world.y1),
              worldToMap(overlay.world.x2, overlay.world.y2),
            ]
          : bounds;
        L.imageOverlay(overlay.src, target).addTo(map);
      };
      probe.src = overlay.src;
    }

    // Grid fallback, drawn immediately and harmlessly sitting under any image
    // that later loads.
    setTimeout(() => {
      if (anyLoaded) return;
      const canvas = document.createElement('canvas');
      canvas.width = canvas.height = 1024;
      const ctx = canvas.getContext('2d')!;
      ctx.fillStyle = '#0b0d11';
      ctx.fillRect(0, 0, 1024, 1024);
      ctx.strokeStyle = '#1c2028';
      ctx.lineWidth = 1;
      for (let i = 0; i <= 1024; i += 64) {
        ctx.beginPath(); ctx.moveTo(i, 0); ctx.lineTo(i, 1024); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(0, i); ctx.lineTo(1024, i); ctx.stroke();
      }
      L.imageOverlay(canvas.toDataURL(), bounds).addTo(map);
    }, 400);

    map.fitBounds(bounds);

    poiLayer.current.addTo(map);
    baseLayer.current.addTo(map);
    playerLayer.current.addTo(map);

    map.on('mousemove', (e) => onMouseMove(e.latlng.lat, e.latlng.lng));
    mapRef.current = map;

    return () => {
      map.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ─── Points of interest ─────────────────────────────────
  useEffect(() => {
    const group = poiLayer.current;
    group.clearLayers();

    const visible = mapObjects.filter((o) => layers[o.category]);
    for (const object of visible.slice(0, MAX_POI_MARKERS)) {
      const style = CATEGORY_STYLE[object.category] ?? { color: '#6d747e', size: 4, label: object.category };
      const coords = worldToGameMap(object.x, object.y);

      L.circleMarker(worldToMap(object.x, object.y), {
        radius: style.size / 2,
        color: style.color,
        weight: 1,
        fillColor: style.color,
        fillOpacity: 0.7,
      })
        .bindPopup(
          `<div style="min-width:150px">
             <div style="font-weight:600;margin-bottom:3px">${style.label}</div>
             <div style="font-size:12px;color:#a1a7b0">${object.kind}</div>
             <div style="font-size:11px;color:#6d747e;margin-top:4px">${coords.x}, ${coords.y}` +
             (object.opened != null ? ` · ${object.opened ? 'opened' : 'unopened'}` : '') +
           `</div>
           </div>`
        )
        .addTo(group);
    }
  }, [mapObjects, layers]);

  // ─── Bases ──────────────────────────────────────────────
  useEffect(() => {
    const group = baseLayer.current;
    group.clearLayers();
    if (!layers.bases) return;

    for (const base of bases) {
      const position = worldToMap(base.x, base.y);
      const coords = worldToGameMap(base.x, base.y);

      // The base's build radius, so you can see actual territory.
      if (base.radius > 0) {
        L.circle(position, {
          radius: base.radius / 459,
          color: '#c9973f',
          weight: 1,
          opacity: 0.5,
          fillColor: '#c9973f',
          fillOpacity: 0.06,
        }).addTo(group);
      }

      L.marker(position, {
        icon: L.divIcon({
          className: 'player-marker',
          html: '<div class="base-marker-icon"></div>',
          iconSize: [14, 14],
          iconAnchor: [7, 7],
        }),
      })
        .bindPopup(
          `<div style="min-width:180px">
             <div style="font-weight:600;margin-bottom:3px">${base.guildName}</div>
             <div style="font-size:12px;color:#a1a7b0">Pals: ${base.palCount}</div>
             <div style="font-size:11px;color:#6d747e;margin-top:4px">${coords.x}, ${coords.y}</div>
           </div>`
        )
        .addTo(group);
    }
  }, [bases, layers.bases]);

  // ─── Players ────────────────────────────────────────────
  useEffect(() => {
    const group = playerLayer.current;
    group.clearLayers();
    if (!layers.players) return;

    for (const player of players) {
      const coords = worldToGameMap(player.location_x, player.location_y);
      L.marker(worldToMap(player.location_x, player.location_y), {
        icon: L.divIcon({
          className: 'player-marker',
          html: '<div class="player-marker-dot"></div>',
          iconSize: [11, 11],
          iconAnchor: [5.5, 5.5],
        }),
        zIndexOffset: 1000,
      })
        .bindPopup(
          `<div style="min-width:150px">
             <div style="font-weight:600;margin-bottom:3px">${player.name}</div>
             <div style="font-size:12px;color:#a1a7b0">Level ${player.level} · ${player.ping}ms</div>
             <div style="font-size:11px;color:#6d747e;margin-top:4px">${coords.x}, ${coords.y}</div>
           </div>`
        )
        .addTo(group);
    }
  }, [players, layers.players]);

  return <div ref={containerRef} style={{ width: '100%', height: '100%' }} />;
}
