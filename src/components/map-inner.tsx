'use client';

import { useEffect, useRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import {
  worldToMap,
  worldToGameMap,
  mapToWorld,
  getRegion,
  MAP_SIZE,
  type MapRegion,
} from '@/lib/map-coordinates';
import type { Player, BaseCamp, MapObject, FastTravelPoint } from '@/lib/types';

interface Props {
  players: Player[];
  bases: BaseCamp[];
  mapObjects: MapObject[];
  fastTravel: FastTravelPoint[];
  layers: Record<string, boolean>;
  region: MapRegion;
  /** World coordinates to pan to, bumped by the search box. */
  flyTo: { x: number; y: number; nonce: number } | null;
  onMouseMove: (worldX: number, worldY: number) => void;
}

/**
 * Marker styling per category. Muted and consistent — the map should read as
 * data, not decoration.
 */
const CATEGORY_STYLE: Record<string, { color: string; size: number; label: string }> = {
  chest:       { color: '#c9973f', size: 6, label: 'Chest' },
  fishingJunk: { color: '#5f6b73', size: 4, label: 'Fishing junk' },
  oilrigChest: { color: '#d97757', size: 7, label: 'Oil rig crate' },
  oreNode:     { color: '#8a8378', size: 5, label: 'Ore / mining node' },
  drop:        { color: '#6d747e', size: 4, label: 'Dropped item' },
  palbox:      { color: '#5b9dd9', size: 8, label: 'Palbox' },
  breeding:    { color: '#8d84c7', size: 7, label: 'Breeding farm' },
  statue:      { color: '#4d9e75', size: 8, label: 'Statue' },
  crafting:    { color: '#a1a7b0', size: 5, label: 'Crafting' },
  production:  { color: '#6d747e', size: 5, label: 'Production' },
  farm:        { color: '#7fa05b', size: 5, label: 'Farm plot' },
  storage:     { color: '#c25757', size: 5, label: 'Storage' },
  comfort:     { color: '#6d747e', size: 4, label: 'Comfort' },
  egg:         { color: '#c9973f', size: 6, label: 'Egg' },
  defense:     { color: '#b0553f', size: 5, label: 'Defense' },
};

/**
 * Thousands of individual Leaflet markers jank the browser. POIs render as
 * canvas circle markers (cheap) and are capped per render; the cap is generous
 * because canvas handles a few thousand fine, unlike DOM markers.
 */
const MAX_POI_MARKERS = 4000;

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c] as string
  );
}

export default function MapInner({
  players,
  bases,
  mapObjects,
  fastTravel,
  layers,
  region,
  flyTo,
  onMouseMove,
}: Props) {
  const mapRef = useRef<L.Map | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const overlayRef = useRef<L.ImageOverlay | null>(null);
  const rendererRef = useRef<L.Canvas | null>(null);

  const poiLayer = useRef<L.LayerGroup>(L.layerGroup());
  const travelLayer = useRef<L.LayerGroup>(L.layerGroup());
  const baseLayer = useRef<L.LayerGroup>(L.layerGroup());
  const playerLayer = useRef<L.LayerGroup>(L.layerGroup());

  // Keep the latest callback without making it an effect dependency.
  const moveRef = useRef(onMouseMove);
  useEffect(() => {
    moveRef.current = onMouseMove;
  }, [onMouseMove]);

  const regionRef = useRef(region);
  useEffect(() => {
    regionRef.current = region;
  }, [region]);

  // ─── Map setup, once ────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const map = L.map(containerRef.current, {
      crs: L.CRS.Simple,
      minZoom: -3,
      maxZoom: 4,
      zoomControl: true,
      attributionControl: false,
      preferCanvas: true,
    });

    rendererRef.current = L.canvas({ padding: 0.5 });

    const bounds: L.LatLngBoundsExpression = [
      [0, 0],
      [MAP_SIZE, MAP_SIZE],
    ];
    map.fitBounds(bounds);
    map.setMaxBounds([
      [-MAP_SIZE * 0.2, -MAP_SIZE * 0.2],
      [MAP_SIZE * 1.2, MAP_SIZE * 1.2],
    ]);

    poiLayer.current.addTo(map);
    travelLayer.current.addTo(map);
    baseLayer.current.addTo(map);
    playerLayer.current.addTo(map);

    map.on('mousemove', (e) => {
      const world = mapToWorld(e.latlng.lat, e.latlng.lng, regionRef.current);
      moveRef.current(world.x, world.y);
    });

    mapRef.current = map;

    return () => {
      map.remove();
      mapRef.current = null;
      overlayRef.current = null;
    };
  }, []);

  // ─── Background image, swapped per region ───────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    const transform = getRegion(region);
    const bounds: L.LatLngBoundsExpression = [
      [0, 0],
      [MAP_SIZE, MAP_SIZE],
    ];

    if (overlayRef.current) {
      map.removeLayer(overlayRef.current);
      overlayRef.current = null;
    }

    // Probe first: a missing image should leave the neutral background rather
    // than a broken-image box. The map still works — markers are positioned by
    // the transform, not the picture.
    const probe = new Image();
    probe.onload = () => {
      if (!mapRef.current) return;
      const overlay = L.imageOverlay(transform.image, bounds, { opacity: 1 });
      overlay.addTo(map);
      overlay.bringToBack();
      overlayRef.current = overlay;
    };
    probe.src = transform.image;
  }, [region]);

  // ─── Points of interest ─────────────────────────────────
  useEffect(() => {
    const group = poiLayer.current;
    group.clearLayers();

    const transform = getRegion(region);
    const visible = mapObjects.filter(
      (o) => layers[o.category] && transform.contains(o.x, o.y)
    );

    for (const object of visible.slice(0, MAX_POI_MARKERS)) {
      const style =
        CATEGORY_STYLE[object.category] ??
        { color: '#6d747e', size: 4, label: object.category };
      const coords = worldToGameMap(object.x, object.y);
      const name = object.name || object.kind;

      L.circleMarker(worldToMap(object.x, object.y, region), {
        renderer: rendererRef.current ?? undefined,
        radius: style.size / 2,
        color: style.color,
        weight: 1,
        fillColor: style.color,
        fillOpacity: 0.7,
      })
        .bindPopup(
          `<div style="min-width:160px">
             <div style="font-weight:600;margin-bottom:3px">${escapeHtml(name)}</div>
             <div style="font-size:12px;color:#a1a7b0">${escapeHtml(style.label)}</div>
             <div style="font-size:11px;color:#6d747e;margin-top:4px">${coords.x}, ${coords.y}` +
            (object.opened != null ? ` · ${object.opened ? 'opened' : 'unopened'}` : '') +
            (object.worldPlaced === false ? ' · in a base' : '') +
            `</div>
           </div>`
        )
        .addTo(group);
    }
  }, [mapObjects, layers, region]);

  // ─── Fast travel (bundled game data, not from the save) ──
  useEffect(() => {
    const group = travelLayer.current;
    group.clearLayers();
    if (!layers.fastTravel) return;

    const transform = getRegion(region);
    for (const point of fastTravel.filter((p) => transform.contains(p.x, p.y))) {
      const coords = worldToGameMap(point.x, point.y);

      L.marker(worldToMap(point.x, point.y, region), {
        icon: L.divIcon({
          className: 'fasttravel-marker',
          html: '<div class="fasttravel-marker-icon"></div>',
          iconSize: [12, 12],
          iconAnchor: [6, 6],
        }),
        zIndexOffset: 500,
      })
        .bindPopup(
          `<div style="min-width:160px">
             <div style="font-weight:600;margin-bottom:3px">${escapeHtml(point.name)}</div>
             <div style="font-size:12px;color:#a1a7b0">Fast travel</div>
             <div style="font-size:11px;color:#6d747e;margin-top:4px">${coords.x}, ${coords.y}</div>
           </div>`
        )
        .addTo(group);
    }
  }, [fastTravel, layers.fastTravel, region]);

  // ─── Bases ──────────────────────────────────────────────
  useEffect(() => {
    const group = baseLayer.current;
    group.clearLayers();
    if (!layers.bases) return;

    const transform = getRegion(region);
    for (const base of bases.filter((b) => transform.contains(b.x, b.y))) {
      const position = worldToMap(base.x, base.y, region);
      const coords = worldToGameMap(base.x, base.y);

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
             <div style="font-weight:600;margin-bottom:3px">${escapeHtml(base.guildName)}</div>
             <div style="font-size:12px;color:#a1a7b0">Pals: ${base.palCount}</div>
             <div style="font-size:11px;color:#6d747e;margin-top:4px">${coords.x}, ${coords.y}</div>
           </div>`
        )
        .addTo(group);
    }
  }, [bases, layers.bases, region]);

  // ─── Players ────────────────────────────────────────────
  useEffect(() => {
    const group = playerLayer.current;
    group.clearLayers();
    if (!layers.players) return;

    const transform = getRegion(region);
    const here = players.filter((p) =>
      transform.contains(p.location_x, p.location_y)
    );

    for (const player of here) {
      const coords = worldToGameMap(player.location_x, player.location_y);
      L.marker(worldToMap(player.location_x, player.location_y, region), {
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
             <div style="font-weight:600;margin-bottom:3px">${escapeHtml(player.name)}</div>
             <div style="font-size:12px;color:#a1a7b0">Level ${player.level} · ${player.ping}ms</div>
             <div style="font-size:11px;color:#6d747e;margin-top:4px">${coords.x}, ${coords.y}</div>
           </div>`
        )
        .addTo(group);
    }
  }, [players, layers.players, region]);

  // ─── Fly to a searched location ─────────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !flyTo) return;
    map.setView(worldToMap(flyTo.x, flyTo.y, region), 2, { animate: true });
  }, [flyTo, region]);

  return <div ref={containerRef} style={{ width: '100%', height: '100%' }} />;
}
