'use client';

import { useEffect, useRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { prettyClass } from '@/lib/pretty-class';
import { kindColor, markerShape, shapeSvg } from '@/lib/kind-colors';
import {
  worldToMap,
  worldToGameMap,
  mapToWorld,
  getRegion,
  MAP_SIZE,
  type MapRegion,
} from '@/lib/map-coordinates';
import type {
  Discoveries, Player, BaseCamp, MapObject, FastTravelPoint,
  StaticWorldObject } from '@/lib/types';

interface Props {
  players: Player[];
  bases: BaseCamp[];
  mapObjects: MapObject[];
  fastTravel: FastTravelPoint[];
  discoveries: Discoveries | null;
  /** Static pak-derived objects for the current viewport. Fetched by the parent. */
  staticObjects: StaticWorldObject[];
  layers: Record<string, boolean>;
  /**
   * Per-category kind exclusions, for the save-derived POI layer.
   *
   * The static (pak-derived) layer was filtered server-side by viewport query;
   * these objects all arrive at once, so the filter is applied here. Same state
   * either way, so one control governs both.
   */
  kindsOff: Record<string, string[]>;
  region: MapRegion;
  /** World coordinates to pan to, bumped by the search box. */
  flyTo: { x: number; y: number; nonce: number } | null;
  onMouseMove: (worldX: number, worldY: number) => void;
  /**
   * The visible area in world coordinates, after a pan or zoom settles.
   *
   * Reported rather than fetched here because the static-object set is 51,921
   * strong and has to be queried by viewport — the map owns the viewport, and the
   * parent owns the data.
   */
  onViewportChange: (box: { minX: number; minY: number; maxX: number; maxY: number }) => void;
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

/**
 * Real game artwork for the *sparse* layers only.
 *
 * Image markers are DOM nodes; the canvas circles used for static objects are
 * not. With up to 2,000 static markers redrawn on every pan, giving those
 * artwork would trade a real amount of scroll smoothness for decoration. Bases,
 * players, fast travel and palboxes number in the tens, so they can afford it —
 * and they are the markers people actually look for.
 *
 * Missing files degrade to the old CSS markers rather than showing a broken
 * image: icons are optional, and a clone that skipped `install-icons.py` must
 * still get a usable map.
 */
const PIN = {
  base: '/icons/game/baseicon.webp',
  player: '/icons/game/playericon.webp',
  palbox: '/icons/structures/T_icon_buildObject_PalBoxV2.webp',
} as const;

/**
 * A live player: the pin, plus a pulsing halo, plus a name label.
 *
 * Separate from `pinIcon` because a player is the one marker on this map that
 * **moves and is being looked for**. Everything else is scenery you scan for at
 * leisure; a live position is the answer to "where is everyone", and a 20px
 * static pin lost that against a busy map — especially zoomed out, where it is
 * 20px against the whole of Palpagos.
 *
 * **Scaling is CSS, not a re-render.** `--player-scale` is set on the map
 * container by the zoom handler, so zooming re-scales markers through the
 * compositor instead of rebuilding them. With `zoomSnap: 0` a single pinch
 * settles many times, and recreating markers on each settle is exactly the cost
 * the static layer's debounce exists to avoid.
 *
 * The icon is deliberately NOT sized here for that reason — `iconSize` and
 * `iconAnchor` stay at the unscaled values, and CSS `transform-origin: center`
 * keeps the visual centre on the anchor at every scale.
 */
function playerIcon(name: string): L.DivIcon {
  return L.divIcon({
    className: 'player-pin',
    html:
      `<span class="player-pin-halo"></span>` +
      `<img src="${PIN.player}" alt="" class="player-pin-img" ` +
      `onerror="this.replaceWith(Object.assign(document.createElement('div'),` +
      `{className:'player-marker-dot'}))">` +
      `<span class="player-pin-name">${escapeHtml(name)}</span>`,
    iconSize: [22, 22],
    iconAnchor: [11, 11],
  });
}

/** An image pin that falls back to a CSS marker when the artwork is absent. */
function pinIcon(src: string, size: number, fallbackClass: string): L.DivIcon {
  return L.divIcon({
    className: 'game-pin',
    html:
      `<img src="${src}" alt="" ` +
      // Size in the inline style, not only as width/height attributes. The
      // source art is up to 512x512 and HTML attributes lose to any stylesheet
      // rule that touches `img`; one that did put 512px statues on the map,
      // which also made every marker look wildly off-centre when zoomed out
      // because the visual centre was nowhere near the 9px anchor point.
      `style="display:block;width:${size}px;height:${size}px;` +
      `object-fit:contain;filter:drop-shadow(0 1px 2px rgba(0,0,0,.6))" ` +
      `onerror="this.replaceWith(Object.assign(document.createElement('div'),` +
      `{className:'${fallbackClass}'}))">`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  });
}

/**
 * Static pak-derived categories. Deliberately smaller and flatter than
 * `CATEGORY_STYLE`: there are an order of magnitude more of these, and they are
 * terrain features rather than anything anyone owns.
 */
const STATIC_STYLE: Record<string, { color: string; size: number; label: string }> = {
  ore:      { color: '#8a8378', size: 4, label: 'Ore / mineral node' },
  treasure: { color: '#c9973f', size: 5, label: 'Treasure chest' },
  fishing:  { color: '#5f6b73', size: 4, label: 'Fishing spot' },
  oilrig:   { color: '#d97757', size: 6, label: 'Oil field' },
  // Spawners are the densest category by far (13,851 of them), so they are
  // drawn smallest and dimmest — a hotspot is useful as a cloud, not as
  // 13,000 individually legible pins.
  palspawner: { color: '#6f9e6a', size: 3, label: 'Pal spawn point' },
  dungeon:  { color: '#9a6fb0', size: 5, label: 'Dungeon' },
  effigy:   { color: '#c7b04a', size: 5, label: 'Lifmunk effigy' },
  npc:      { color: '#c9a227', size: 5, label: 'NPC / camp' },
};


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
  discoveries,
  staticObjects,
  layers,
  kindsOff,
  region,
  flyTo,
  onMouseMove,
  onViewportChange,
}: Props) {
  const mapRef = useRef<L.Map | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const overlayRef = useRef<L.ImageOverlay | null>(null);
  const rendererRef = useRef<L.Canvas | null>(null);

  const poiLayer = useRef<L.LayerGroup>(L.layerGroup());
  const staticLayer = useRef<L.LayerGroup>(L.layerGroup());
  const travelLayer = useRef<L.LayerGroup>(L.layerGroup());
  const effigyLayer = useRef<L.LayerGroup>(L.layerGroup());
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

  const viewportRef = useRef(onViewportChange);
  useEffect(() => {
    viewportRef.current = onViewportChange;
  }, [onViewportChange]);

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

      // Leaflet's defaults are tuned for tiled street maps, where a zoom level
      // is a discrete set of tiles and snapping to one is correct. This is a
      // single image overlay with no tile pyramid, so snapping buys nothing and
      // costs a lot: at `zoomSnap: 1` every wheel notch jumps a whole level —
      // a 2x scale change in one frame — which reads as the map lurching rather
      // than zooming.
      zoomSnap: 0,               // continuous zoom; no quantising to integers
      zoomDelta: 0.5,            // the +/- buttons and keyboard step by half
      wheelPxPerZoomLevel: 120,  // default 60; halves wheel sensitivity
      wheelDebounceTime: 20,     // default 40; lower feels more responsive
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

    // Static objects sit under everything else: there are far more of them than
    // anything player-owned, and a base marker buried under ore is useless.
    staticLayer.current.addTo(map);
    poiLayer.current.addTo(map);
    travelLayer.current.addTo(map);
    effigyLayer.current.addTo(map);
    baseLayer.current.addTo(map);
    playerLayer.current.addTo(map);

    map.on('mousemove', (e) => {
      const world = mapToWorld(e.latlng.lat, e.latlng.lng, regionRef.current);
      moveRef.current(world.x, world.y);
    });

    // `moveend` fires once a pan or zoom settles, not on every frame of it, so
    // dragging across the map is one request rather than sixty.
    //
    // The trailing debounce is for continuous zoom: with `zoomSnap: 0` a single
    // trackpad pinch settles several times, and each settle refetches the
    // viewport and rebuilds every static marker. Coalescing them means one
    // rebuild per gesture instead of one per intermediate stop.
    let settleTimer: ReturnType<typeof setTimeout> | null = null;
    const report = () => {
      const bounds = map.getBounds();
      const a = mapToWorld(bounds.getSouth(), bounds.getWest(), regionRef.current);
      const b = mapToWorld(bounds.getNorth(), bounds.getEast(), regionRef.current);
      viewportRef.current({
        minX: Math.min(a.x, b.x), minY: Math.min(a.y, b.y),
        maxX: Math.max(a.x, b.x), maxY: Math.max(a.y, b.y),
      });
    };
    const reportSoon = () => {
      if (settleTimer) clearTimeout(settleTimer);
      settleTimer = setTimeout(report, 150);
    };
    map.on('moveend', reportSoon);
    map.on('zoomend', reportSoon);
    report();

    // Live-player markers grow as you zoom out.
    //
    // A fixed 20px pin is fine at zoom 4 and nearly invisible at -3, where it is
    // 20px against an entire landmass — which is the zoom people are actually at
    // when they ask "where is everyone". Scaling up as you zoom out inverts that:
    // the marker keeps roughly the same *screen* prominence at every zoom.
    //
    // Written to a CSS variable rather than into each icon so this runs on
    // `zoom` (every frame of the gesture, cheap: one style write) instead of on
    // `zoomend` after a rebuild. Markers are never recreated.
    const container = map.getContainer();
    const applyScale = () => {
      const zoom = map.getZoom();
      // 2.6x at fully zoomed out, tapering to 1x by about zoom 2. Measured by
      // eye against the Palpagos texture rather than derived — the useful
      // constraint is "findable without covering the terrain".
      const scale = Math.min(2.6, Math.max(1, 2.6 - 0.28 * (zoom + 3)));
      container.style.setProperty('--player-scale', scale.toFixed(2));
      // Names are unreadable at a distance and clutter the map; they earn their
      // space only once you are close enough to care which player is which.
      container.classList.toggle('show-player-names', zoom >= 0);
    };
    map.on('zoom', applyScale);
    applyScale();

    mapRef.current = map;

    return () => {
      // Before map.remove(), or a pending settle fires into a torn-down map.
      if (settleTimer) clearTimeout(settleTimer);
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
      (o) =>
        layers[o.category] &&
        !(kindsOff[o.category] ?? []).includes(o.kind) &&
        transform.contains(o.x, o.y)
    );

    for (const object of visible.slice(0, MAX_POI_MARKERS)) {
      const style =
        CATEGORY_STYLE[object.category] ??
        { color: '#6d747e', size: 4, label: object.category };
      const coords = worldToGameMap(object.x, object.y);
      const name = object.name || object.kind;
      // Colour says which kind, shape says which category. Circles stay on the
      // canvas renderer; anything else needs a DOM marker, which is why only
      // the sparse categories get one.
      const color = kindColor(object.kind, style.color);
      const shape = markerShape(object.category);

      if (shape !== 'circle') {
        L.marker(worldToMap(object.x, object.y, region), {
          icon: L.divIcon({
            className: 'shape-marker',
            html: shapeSvg(shape, style.size + 2, color),
            iconSize: [style.size + 2, style.size + 2],
            iconAnchor: [(style.size + 2) / 2, (style.size + 2) / 2],
          }),
        })
          .bindPopup(
            `<div style="min-width:150px">
               <div style="font-weight:600;margin-bottom:3px">${escapeHtml(name)}</div>
               <div style="font-size:12px;color:#a1a7b0">${escapeHtml(style.label)}</div>
               <div style="font-size:11px;color:#6d747e;margin-top:4px">${coords.x}, ${coords.y}</div>
             </div>`
          )
          .addTo(group);
        continue;
      }

      L.circleMarker(worldToMap(object.x, object.y, region), {
        renderer: rendererRef.current ?? undefined,
        radius: style.size / 2,
        color,
        weight: 1,
        fillColor: color,
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
  }, [mapObjects, layers, kindsOff, region]);

  // ─── Static world objects (pak-derived, viewport-scoped) ─
  //
  // These are every ore node, chest and fishing spot the game ships, not the
  // handful a save happens to have state for — 51,921 in total, which is why the
  // parent fetches only what is in view. Drawn smaller and dimmer than
  // save-derived markers, because "a rock exists here" is background information
  // next to "someone's palbox is here".
  useEffect(() => {
    const group = staticLayer.current;
    group.clearLayers();

    const transform = getRegion(region);
    for (const object of staticObjects) {
      if (!layers[`static:${object.category}`]) continue;
      if (!transform.contains(object.x, object.y)) continue;

      const style = STATIC_STYLE[object.category] ??
        { color: '#6d747e', size: 3, label: object.category };
      // Per *kind*, not per category: 17 rocks all drawn in one grey told you
      // nothing about which was copper and which was coal.
      const color = kindColor(object.cls, style.color);
      const shape = markerShape(object.category);

      // Ore and spawners stay canvas circles no matter what: there are 24,359
      // and 13,851 of them, DOM markers at that count jank the browser, and at
      // that density shape is indistinguishable anyway.
      if (shape !== 'circle' && object.category !== 'ore' && object.category !== 'palspawner') {
        L.marker(worldToMap(object.x, object.y, region), {
          icon: L.divIcon({
            className: 'shape-marker',
            html: shapeSvg(shape, style.size + 1, color),
            iconSize: [style.size + 1, style.size + 1],
            iconAnchor: [(style.size + 1) / 2, (style.size + 1) / 2],
          }),
          opacity: 0.85,
        })
          .bindPopup(() => {
            const c = worldToGameMap(object.x, object.y);
            return `<div style="min-width:150px">
               <div style="font-weight:600;margin-bottom:3px">${escapeHtml(prettyClass(object.cls))}</div>
               <div style="font-size:12px;color:#a1a7b0">${escapeHtml(style.label)}</div>
               <div style="font-size:11px;color:#6d747e;margin-top:4px">${c.x}, ${c.y}</div>
             </div>`;
          })
          .addTo(group);
        continue;
      }

      L.circleMarker(worldToMap(object.x, object.y, region), {
        renderer: rendererRef.current ?? undefined,
        radius: style.size / 2,
        color,
        weight: 0,
        fillColor: color,
        fillOpacity: 0.55,
      })
        // Built on open, not on draw. This layer is rebuilt on every pan and
        // can hold 2,000 markers, so eagerly formatting 2,000 popups meant
        // 2,000 string builds and coordinate conversions per gesture for
        // content that is almost never read.
        .bindPopup(() => {
          const coords = worldToGameMap(object.x, object.y);
          return `<div style="min-width:150px">
             <div style="font-weight:600;margin-bottom:3px">${escapeHtml(prettyClass(object.cls))}</div>
             <div style="font-size:12px;color:#a1a7b0">${escapeHtml(style.label)}</div>
             <div style="font-size:11px;color:#6d747e;margin-top:4px">${coords.x}, ${coords.y}</div>
           </div>`;
        })
        .addTo(group);
    }
  }, [staticObjects, layers, region]);

  // ─── Fast travel (bundled game data, not from the save) ──
  //
  // When discovery data is available it supersedes the plain list, because it
  // carries the same points *plus* whether each has been found. Undiscovered
  // ones are dimmed rather than hidden — the server already decided whether to
  // send them at all, so anything that arrives here is meant to be seen.
  useEffect(() => {
    const group = travelLayer.current;
    group.clearLayers();
    if (!layers.fastTravel) return;

    const transform = getRegion(region);
    const points = discoveries
      ? discoveries.fastTravel.points
      : fastTravel.map((p) => ({ ...p, discovered: true }));

    // The same per-kind filter every other layer has. Tower entrances, watch-
    // towers and ordinary points share one layer and one marker, so "show me
    // only the towers" had no expression here while it worked for ore.
    const off = kindsOff.fastTravel ?? [];

    for (const point of points.filter((p) => transform.contains(p.x, p.y))) {
      const kind = point.kind ?? 'travel';
      if (off.includes(kind)) continue;

      const coords = worldToGameMap(point.x, point.y);
      const found = point.discovered;
      const label =
        kind === 'tower' ? 'Tower boss' : kind === 'watchtower' ? 'Watchtower' : 'Fast travel';

      L.marker(worldToMap(point.x, point.y, region), {
        // The game's own fast-travel art is a dark stone plinth, which is
        // illegible at 18px against a map and reads as a black blob. The
        // purpose-built marker below is a gold diamond that says "fast travel"
        // at a glance, so this layer deliberately keeps CSS rather than art —
        // real artwork is not automatically the better choice at marker size.
        icon: L.divIcon({
          className: 'fasttravel-marker',
          html: `<div class="fasttravel-marker-icon is-${kind}"${found ? '' : ' style="opacity:.35;filter:grayscale(1)"'}></div>`,
          iconSize: [12, 12],
          iconAnchor: [6, 6],
        }),
        // Towers above everything else in the layer: there are eight of them
        // among 174 and they are what people are looking for.
        zIndexOffset: (found ? 500 : 400) + (kind === 'tower' ? 60 : 0),
      })
        .bindPopup(
          `<div style="min-width:160px">
             <div style="font-weight:600;margin-bottom:3px">${escapeHtml(point.name ?? '')}</div>
             <div style="font-size:12px;color:${found ? '#4d9e75' : '#a1a7b0'}">
               ${label} — ${found ? 'unlocked' : 'not yet found'}
             </div>
             <div style="font-size:11px;color:#6d747e;margin-top:4px">${coords.x}, ${coords.y}</div>
           </div>`
        )
        .addTo(group);
    }
  }, [fastTravel, discoveries, layers.fastTravel, kindsOff, region]);

  // ─── Effigies ───────────────────────────────────────────
  useEffect(() => {
    const group = effigyLayer.current;
    group.clearLayers();
    if (!layers.effigies || !discoveries) return;

    const transform = getRegion(region);
    for (const point of discoveries.effigies.points.filter((p) => transform.contains(p.x, p.y))) {
      const coords = worldToGameMap(point.x, point.y);
      const found = point.discovered;

      L.circleMarker(worldToMap(point.x, point.y, region), {
        radius: 4,
        color: found ? '#4d9e75' : '#8d84c7',
        weight: 1,
        fillColor: found ? '#4d9e75' : '#8d84c7',
        fillOpacity: found ? 0.85 : 0.25,
      })
        .bindPopup(
          `<div style="min-width:150px">
             <div style="font-weight:600;margin-bottom:3px">Effigy</div>
             <div style="font-size:12px;color:${found ? '#4d9e75' : '#a1a7b0'}">
               ${found ? 'Collected' : 'Not collected'}
             </div>
             <div style="font-size:11px;color:#6d747e;margin-top:4px">${coords.x}, ${coords.y}</div>
           </div>`
        )
        .addTo(group);
    }
  }, [discoveries, layers.effigies, region]);

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

      L.marker(position, { icon: pinIcon(PIN.base, 22, 'base-marker-icon') })
        .bindPopup(
          `<div style="min-width:180px">
             <div style="font-weight:600;margin-bottom:3px">${escapeHtml(base.guildName)}</div>
             <div style="font-size:12px;color:#a1a7b0">${base.palCount ?? 0} Pals working &middot; ${base.guildPalCount ?? 0} in guild</div>
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
        icon: playerIcon(player.name),
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
