'use client';

import { useEffect, useRef, useState } from 'react';
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
  Discoveries, DiscoveryPoint, Player, BaseCamp, MapObject, FastTravelPoint,
  StaticWorldObject } from '@/lib/types';

interface Props {
  players: Player[];
  bases: BaseCamp[];
  mapObjects: MapObject[];
  fastTravel: FastTravelPoint[];
  discoveries: Discoveries | null;
  /**
   * Effigies without the found/not-found join — the fallback for when
   * `discoveries` is null, which happens to every guest because that route needs
   * a real account. Fast travel has always had `fastTravel` to fall back to;
   * effigies had nothing, so the layer vanished with no error to follow.
   */
  effigies: DiscoveryPoint[];
  /**
   * Completion mode: drop anything already collected from the one-time layers.
   *
   * Only ever hides a point whose status is KNOWN to be collected. A point the
   * server did not tell us about — the fallback endpoints leave `discovered`
   * undefined on purpose — stays on the map, because hiding on unknown status
   * would remove exactly the markers someone in completion mode is hunting.
   */
  hideCollected: boolean;
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
 * How much bigger every category marker is than its original size.
 *
 * One constant rather than twenty-five edited numbers, because the *relative*
 * sizes below are deliberate — a palbox outranks a farm plot — and bumping them
 * individually is how that ordering quietly gets lost.
 *
 * The original values were tuned as "the map should read as data, not
 * decoration", which overshot: at 3-8 px, on a textured satellite image, most
 * markers were genuinely hard to find. Restraint about *colour* is what keeps a
 * map readable; being too small to see is not restraint.
 */
const MARKER_SCALE = 1.6;

const px = (size: number) => Math.round(size * MARKER_SCALE);

/**
 * Marker styling per category. Muted and consistent — the map should read as
 * data, not decoration.
 */
const CATEGORY_STYLE: Record<string, { color: string; size: number; label: string }> = {
  chest:       { color: '#c9973f', size: px(6), label: 'Chest' },
  fishingJunk: { color: '#5f6b73', size: px(4), label: 'Fishing junk' },
  oilrigChest: { color: '#d97757', size: px(7), label: 'Oil rig crate' },
  oreNode:     { color: '#8a8378', size: px(5), label: 'Ore / mining node' },
  drop:        { color: '#6d747e', size: px(4), label: 'Dropped item' },
  palbox:      { color: '#5b9dd9', size: px(8), label: 'Palbox' },
  breeding:    { color: '#8d84c7', size: px(7), label: 'Breeding farm' },
  statue:      { color: '#4d9e75', size: px(8), label: 'Statue' },
  crafting:    { color: '#a1a7b0', size: px(5), label: 'Crafting' },
  production:  { color: '#6d747e', size: px(5), label: 'Production' },
  farm:        { color: '#7fa05b', size: px(5), label: 'Farm plot' },
  storage:     { color: '#c25757', size: px(5), label: 'Storage' },
  comfort:     { color: '#6d747e', size: px(4), label: 'Comfort' },
  egg:         { color: '#c9973f', size: px(6), label: 'Egg' },
  defense:     { color: '#b0553f', size: px(5), label: 'Defense' },
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
  base: '/icons/map/base.webp',
  player: '/icons/map/player.webp',
  palbox: '/icons/structures/T_icon_buildObject_PalBoxV2.webp',
  // Fast travel finally has real artwork. The game's *own* fast-travel asset
  // (`structures/T_icon_buildObject_FastTravelPoint.webp`) is a 512px stone
  // plinth measured at luma 8 of 255 — a black blob at marker size, which is why
  // it was rejected before. These are the compass HUD icons, drawn to be read
  // small. See `public/icons/map/PROVENANCE.md`.
  fastTravel: '/icons/map/fasttravel.webp',
  tower: '/icons/map/tower.webp',
  fieldboss: '/icons/map/fieldboss.webp',
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
 * **The scale goes on an inner wrapper, never on the root.** Leaflet positions
 * `.player-pin` with an *inline* `transform: translate3d(…)`, and an inline
 * style beats a stylesheet rule — so a `transform: scale()` written against the
 * root silently does nothing, which is exactly what the first version did. The
 * wrapper is a separate element precisely so the two transforms cannot fight.
 *
 * `iconSize` and `iconAnchor` stay at the unscaled values, and the wrapper is
 * centred on them with `transform-origin: center`, so growing the marker does
 * not drift it off the position it is reporting.
 */
function playerIcon(name: string): L.DivIcon {
  return L.divIcon({
    className: 'player-pin',
    html:
      `<span class="player-pin-inner">` +
      `<span class="player-pin-halo"></span>` +
      // Background image for the same reason as `pinIcon`: an `<img>` that fails
      // shows a broken-file glyph, and the inline `onerror` that would prevent
      // it is CSP-blockable. `.player-marker-dot` shows through underneath.
      `<span class="player-marker-dot player-pin-img"></span>` +
      `<span class="player-pin-name">${escapeHtml(name)}</span>` +
      `</span>`,
    iconSize: [22, 22],
    iconAnchor: [11, 11],
  });
}

/**
 * A field boss: the Pal's own artwork in a red ring.
 *
 * DOM markers are the expensive kind, which is why the static layer uses canvas
 * circles — but there are **99 of these in the entire world**, so the rule that
 * keeps 24,359 ore nodes cheap does not apply. They are also the single most
 * "where is it" marker on the map, which is what artwork is for.
 *
 * Falls back to a plain ring when the species could not be resolved (2 of 73
 * sheets) or has no icon, rather than rendering a broken image.
 */
function bossIcon(icon: string | undefined, size: number): L.DivIcon {
  // Background image, not `<img>` — see `pinIcon`. A boss whose species did not
  // resolve simply shows the ring with nothing in it.
  // Falls back to the game's generic alpha-Pal map icon for the two of 73
  // sheets whose species did not resolve, rather than an empty ring.
  const art =
    `<span class="boss-pin-art" style="${artStyle(icon || PIN.fieldboss)}"></span>`;
  return L.divIcon({
    className: 'boss-pin',
    html: `<span class="boss-pin-ring">${art}</span>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  });
}

/**
 * An image pin that degrades to a CSS marker when the artwork will not load.
 *
 * **A `background-image`, not an `<img>`, and that is the whole point.** An
 * `<img>` whose source fails renders the browser's broken-file glyph, and the
 * only way to avoid that is an `onerror` handler — which, written inline into a
 * `divIcon`'s HTML, is exactly the thing a Content-Security-Policy blocks. So
 * the failure mode was: image fails, handler never runs, torn-paper icon on the
 * map, and no way to tell from the server side that anything was wrong.
 *
 * A background-image cannot do that. If it loads you see the art; if it does not
 * you see the fallback class underneath it, which is a styled div that was going
 * to be the answer anyway. No JavaScript, no handler, nothing to block.
 *
 * Size stays in the inline style: the source art is up to 512x512 and HTML
 * attributes lose to any stylesheet rule that touches the element — one that did
 * put 512px statues on the map, which also made every marker look wildly
 * off-centre when zoomed out because the visual centre was nowhere near the
 * anchor point.
 */
function pinIcon(src: string, size: number, fallbackClass: string): L.DivIcon {
  return L.divIcon({
    className: 'game-pin',
    html:
      `<span class="${fallbackClass} game-pin-art" ` +
      `style="width:${size}px;height:${size}px;` +
      `${artStyle(src)}"></span>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  });
}

/**
 * ARTWORK THAT LOADED. The fallback above could not fire, and this is why.
 *
 * `pinIcon` writes the fallback class and the `background-image` onto the same
 * element, and `globals.css` switches the fallback's own paint off whenever that
 * element carries a background image — added because an amber square was showing
 * around the edges of a loaded icon, which reads as broken.
 *
 * But the selector is `[style*="background-image"]`, and the style is written
 * unconditionally. So the rule matched whether or not the file existed, and a
 * 404 gave a marker with **no artwork and no shape**: an empty 22px box. Bases
 * vanished from the map while `/api/bases` was answering perfectly and the Bases
 * tab listed every one of them — a rendering failure wearing the costume of a
 * data failure, which is the same disguise `.catch(() => [])` was wearing.
 *
 * The fix is to find out. Each source is fetched once, and the URL is written
 * into the marker only if it resolved. A missing file now leaves the amber
 * square standing, which is what every comment in this file already claimed
 * would happen.
 *
 * Still no inline `onerror` — that is what a CSP blocks, and avoiding it is why
 * this is a background image rather than an `<img>` in the first place. `Image()`
 * is script we own, running under the page's own policy.
 */
const artLoaded = new Map<string, boolean>();

function artStyle(src: string): string {
  return artLoaded.get(src) === false ? '' : `background-image:url('${src}')`;
}

/**
 * Probe every pin source once, and re-render when the answers are in.
 *
 * Sources are known up front, so this runs once on mount rather than per marker
 * — 6 requests, all of which the browser has cached by the second render.
 * Unprobed sources are treated as present: the common case is that they load,
 * and flashing every marker through its fallback on first paint would be a
 * worse map than the one this fixes.
 */
function useArtwork(sources: readonly string[]): number {
  const [settled, setSettled] = useState(0);

  useEffect(() => {
    const pending = sources.filter((s) => !artLoaded.has(s));
    if (pending.length === 0) return;

    let live = true;
    let done = 0;
    const finish = (src: string, ok: boolean) => {
      artLoaded.set(src, ok);
      done += 1;
      if (live && done === pending.length) setSettled((n) => n + 1);
    };
    for (const src of pending) {
      const probe = new Image();
      probe.onload = () => finish(src, true);
      probe.onerror = () => finish(src, false);
      probe.src = src;
    }
    return () => {
      live = false;
    };
  }, [sources]);

  return settled;
}

const PIN_SOURCES = Object.values(PIN);

/**
 * Static pak-derived categories. Deliberately smaller and flatter than
 * `CATEGORY_STYLE`: there are an order of magnitude more of these, and they are
 * terrain features rather than anything anyone owns.
 */
const STATIC_STYLE: Record<string, { color: string; size: number; label: string }> = {
  ore:      { color: '#8a8378', size: px(4), label: 'Ore / mineral node' },
  treasure: { color: '#c9973f', size: px(5), label: 'Treasure chest' },
  fishing:  { color: '#5f6b73', size: px(4), label: 'Fishing spot' },
  oilrig:   { color: '#d97757', size: px(6), label: 'Oil field' },
  // Spawners are the densest category by far (13,851 of them), so they stay the
  // smallest and dimmest — a hotspot is useful as a cloud, not as 13,000
  // individually legible pins. Scaled with everything else so the *ordering*
  // holds; it is still the bottom of it.
  palspawner: { color: '#6f9e6a', size: px(3), label: 'Pal spawn point' },
  dungeon:  { color: '#9a6fb0', size: px(5), label: 'Dungeon' },
  effigy:   { color: '#c7b04a', size: px(5), label: 'Lifmunk effigy' },
  npc:      { color: '#c9a227', size: px(5), label: 'NPC / camp' },
  skillfruit:  { color: '#d98cc4', size: px(6), label: 'Skill / kinship fruit' },
  lotus:       { color: '#7fd4c1', size: px(5), label: 'Stat lotus' },
  junk:        { color: '#8a7a5f', size: px(4), label: 'Junk pile' },
  collectible: { color: '#e0c060', size: px(5), label: 'Coin / pot' },
  supply:      { color: '#5b9dd9', size: px(5), label: 'Supply drop' },
  fieldboss: { color: '#d14b4b', size: px(9), label: 'Field boss' },
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
  effigies,
  hideCollected,
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

  // Redraws the artwork layers once, when the probes come back. Markers built
  // before then assume the art is there, which is right in every case except the
  // one this exists to survive.
  const artSettled = useArtwork(PIN_SOURCES);

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
    // Written to a CSS variable so markers are never recreated to re-scale.
    //
    // **On `zoomend`, not `zoom`, and that is the whole point.** Writing a
    // custom property and toggling a class on `.leaflet-container` invalidates
    // style for the entire map subtree — every pane, every marker. Doing that on
    // `zoom`, which fires on every frame of the animation, made the map visibly
    // lurch sideways mid-gesture and snap back when it settled. The scale does
    // not need to be continuous: markers holding their previous size for the
    // duration of a zoom is both cheaper and calmer.
    //
    // The redundancy guard matters for the same reason — `zoomSnap: 0` settles
    // several times per gesture, and most of those settles do not change the
    // bucket the scale falls in.
    const container = map.getContainer();
    let lastScale = '';
    let lastNames: boolean | null = null;
    const applyScale = () => {
      const zoom = map.getZoom();
      // 2.6x at fully zoomed out, tapering to 1x by about zoom 2. Measured by
      // eye against the Palpagos texture rather than derived — the useful
      // constraint is "findable without covering the terrain".
      const scale = Math.min(2.6, Math.max(1, 2.6 - 0.28 * (zoom + 3))).toFixed(2);
      if (scale !== lastScale) {
        container.style.setProperty('--player-scale', scale);
        lastScale = scale;
      }
      // Names are unreadable at a distance and clutter the map; they earn their
      // space only once you are close enough to care which player is which.
      const names = zoom >= 0;
      if (names !== lastNames) {
        container.classList.toggle('show-player-names', names);
        lastNames = names;
      }
    };
    map.on('zoomend', applyScale);
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

      // Field bosses get the Pal's own artwork. 99 in the world, so the
      // DOM-marker cost that rules artwork out everywhere else is irrelevant
      // here — and these are the markers people are actually hunting for.
      if (object.category === 'fieldboss') {
        const label = object.speciesName || prettyClass(object.cls);
        L.marker(worldToMap(object.x, object.y, region), {
          icon: bossIcon(object.icon, px(26)),
          zIndexOffset: 700,
        })
          .bindPopup(() => {
            const c = worldToGameMap(object.x, object.y);
            return `<div style="min-width:170px">
               <div style="font-weight:600;margin-bottom:3px">${escapeHtml(label)}</div>
               <div style="font-size:12px;color:#d14b4b">Field boss &middot; drops Ancient Technology</div>
               <div style="font-size:11px;color:#6d747e;margin-top:4px">${c.x}, ${c.y}</div>
             </div>`;
          })
          .addTo(group);
        continue;
      }

      // Ore and spawners stay canvas circles no matter what: there are 24,359
      // and 13,752 of them, DOM markers at that count jank the browser, and at
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
        // A dark edge, not `weight: 0`. This was the bigger half of the
        // legibility problem: a 55%-opacity fill with no outline dissolves into
        // a textured satellite image at any size, so enlarging alone would not
        // have fixed it. The stroke is what separates a marker from terrain.
        color: 'rgba(0,0,0,.55)',
        weight: 1,
        fillColor: color,
        fillOpacity: 0.85,
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
  }, [staticObjects, layers, region, artSettled]);

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
      // `=== true`, not truthiness: the fallback list above marks every point
      // `discovered: true` because it cannot know, and only a real join from
      // `/world/discoveries` should be allowed to hide anything.
      if (hideCollected && discoveries && point.discovered === true) continue;

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
        // Real artwork over the CSS shape, as a background image so a failed
        // load degrades to that shape rather than to a broken-file glyph. The
        // marker keeps its size and anchor either way.
        icon: L.divIcon({
          className: 'fasttravel-marker',
          html:
            `<div class="fasttravel-marker-icon is-${kind}" style="` +
            `${artStyle(kind === 'tower' ? PIN.tower : PIN.fastTravel)}` +
            `${found ? '' : ';opacity:.4;filter:grayscale(1)'}"></div>`,
          iconSize: kind === 'tower' ? [22, 22] : [16, 16],
          iconAnchor: kind === 'tower' ? [11, 11] : [8, 8],
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
  }, [fastTravel, discoveries, layers.fastTravel, kindsOff, region, hideCollected, artSettled]);

  // ─── Effigies ───────────────────────────────────────────
  useEffect(() => {
    const group = effigyLayer.current;
    group.clearLayers();
    if (!layers.effigies) return;

    // `discoveries` carries the collected/not-collected join; `effigies` is the
    // bare point list. Falling back to the second keeps the layer on the map when
    // the first is unavailable — and it deliberately leaves `discovered`
    // undefined rather than defaulting it, because "we could not ask" and "you
    // have not collected this" must not share a colour on a collectathon map.
    const points: DiscoveryPoint[] = discoveries
      ? discoveries.effigies.points
      : effigies;
    if (points.length === 0) return;

    const transform = getRegion(region);
    for (const point of points.filter((p) => transform.contains(p.x, p.y))) {
      const coords = worldToGameMap(point.x, point.y);
      const found = point.discovered;
      const unknown = found === undefined;
      // An effigy whose status is unknown survives completion mode — see the
      // prop's comment. This is the layer where it matters most: 396 of them,
      // and the whole reason to switch the mode on is to find the missing ones.
      if (hideCollected && found === true) continue;

      L.circleMarker(worldToMap(point.x, point.y, region), {
        radius: px(4) / 2 + 2,
        // A dark edge on both states. An uncollected effigy at 25% fill and a
        // matching outline was nearly invisible, which defeated the point — the
        // ones you have *not* found are the ones you are looking for.
        color: 'rgba(0,0,0,.55)',
        weight: 1,
        fillColor: unknown ? '#8d84c7' : found ? '#4d9e75' : '#8d84c7',
        fillOpacity: unknown ? 0.7 : found ? 0.9 : 0.5,
      })
        .bindPopup(
          `<div style="min-width:150px">
             <div style="font-weight:600;margin-bottom:3px">${escapeHtml(point.kindName || 'Effigy')}</div>
             <div style="font-size:12px;color:${found ? '#4d9e75' : '#a1a7b0'}">
               ${unknown ? 'Collection status unavailable' : found ? 'Collected' : 'Not collected'}
             </div>
             <div style="font-size:11px;color:#6d747e;margin-top:4px">${coords.x}, ${coords.y}</div>
           </div>`
        )
        .addTo(group);
    }
  }, [discoveries, effigies, layers.effigies, region, hideCollected]);

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
  }, [bases, layers.bases, region, artSettled]);

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
