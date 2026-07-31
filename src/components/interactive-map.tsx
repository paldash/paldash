'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import MapLayersPanel from '@/components/map-layers-panel';
import { useDashboardStore } from '@/lib/store';
import { formatCoords, getRegion, MAP_REGIONS, type MapRegion } from '@/lib/map-coordinates';
import {
  getMapObjects, getFastTravelPoints, getDiscoveries,
  getStaticWorldObjects, getStaticWorldSummary,
} from '@/lib/save-api';
import { Crosshair, RefreshCw, Search, Info } from 'lucide-react';
import dynamic from 'next/dynamic';
import BuildBanner from './build-banner';
import type {
  MapObject, FastTravelPoint, Discoveries,
  StaticWorldObject, StaticWorldSummary,
} from '@/lib/types';

const MapComponent = dynamic(() => import('./map-inner'), { ssr: false });

/**
 * Layer toggles.
 *
 * `fastTravel` is the one layer that does not come from the save — fast-travel
 * statues are static level actors, so their positions ship with the dashboard as
 * bundled game data. Everything else is read out of the world.
 */
const LAYERS: { id: string; label: string; color: string; group: 'live' | 'discovery' | 'world' | 'static' | 'base' }[] = [
  { id: 'players', label: 'Players', color: '#5b9dd9', group: 'live' },
  { id: 'bases', label: 'Bases', color: '#c9973f', group: 'live' },

  // Their own group, because they are neither of the other two and sat under
  // "From the save" claiming to be something they are not. Positions ship with
  // the dashboard (extracted from the pak); the save contributes only which ones
  // *you* have found. The comment above said exactly this while the group label
  // said otherwise.
  //
  // The group also maps one-to-one onto `discoveryCategoryVisibility`, so what
  // an operator sets on the Access tab and what a player sees here are named the
  // same thing.
  { id: 'fastTravel', label: 'Fast travel', color: '#e0c060', group: 'discovery' },
  { id: 'effigies', label: 'Effigies', color: '#8d84c7', group: 'discovery' },

  { id: 'chest', label: 'Chests', color: '#c9973f', group: 'world' },
  { id: 'oreNode', label: 'Ore nodes', color: '#8a8378', group: 'world' },
  { id: 'oilrigChest', label: 'Oil rig', color: '#d97757', group: 'world' },
  { id: 'fishingJunk', label: 'Fishing junk', color: '#5f6b73', group: 'world' },

  // Pak-derived, and a different thing from the save-derived layers above: these
  // are every node the game ships, not the ones a save has state for. Namespaced
  // `static:` so the two can be toggled independently and never collide.
  { id: 'static:ore', label: 'All ore', color: '#8a8378', group: 'static' },
  { id: 'static:treasure', label: 'All chests', color: '#c9973f', group: 'static' },
  { id: 'static:fishing', label: 'Fishing spots', color: '#5f6b73', group: 'static' },
  { id: 'static:oilrig', label: 'Oil fields', color: '#d97757', group: 'static' },
  // Extracted all along and never given a toggle, so 2,163 dungeon objects and
  // 13,851 spawners sat in the bundle unreachable. A category the backend
  // withholds still gets no toggle — `visibleStaticIds` filters this list.
  { id: 'static:dungeon', label: 'Dungeons', color: '#9a6fb0', group: 'static' },
  { id: 'static:palspawner', label: 'Pal spawns', color: '#7fa05b', group: 'static' },
  { id: 'static:npc', label: 'NPCs & camps', color: '#c9a227', group: 'static' },
  // The alpha Pals that drop Ancient Technology Points. 99 in the world, named
  // and drawn with the Pal's own artwork — they were previously indistinguishable
  // from the other 13,851 spawn points.
  { id: 'static:fieldboss', label: 'Field bosses', color: '#d14b4b', group: 'static' },
  // Found by a coverage check over every placeable class in the pak, after a
  // community map showed content this one did not. Extracted from the same pak
  // as everything else rather than copied from anyone's marker data.
  { id: 'static:skillfruit', label: 'Skill & kinship fruit', color: '#d98cc4', group: 'static' },
  { id: 'static:lotus', label: 'Stat lotuses', color: '#7fd4c1', group: 'static' },
  { id: 'static:junk', label: 'Junk piles', color: '#8a7a5f', group: 'static' },
  { id: 'static:collectible', label: 'Coins & pots', color: '#e0c060', group: 'static' },
  { id: 'static:supply', label: 'Supply drops', color: '#5b9dd9', group: 'static' },

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
  const {
    onlinePlayers, bases, mapLayers, toggleMapLayer,
    staticKindsOff, toggleStaticKind, setStaticKindsOff,
  } = useDashboardStore();
  const [mouseCoords, setMouseCoords] = useState<{ x: number; y: number } | null>(null);
  const [mapObjects, setMapObjects] = useState<MapObject[]>([]);
  const [fastTravel, setFastTravel] = useState<FastTravelPoint[]>([]);
  const [discoveries, setDiscoveries] = useState<Discoveries | null>(null);
  const [region, setRegion] = useState<MapRegion>('palpagos');
  const [query, setQuery] = useState('');
  const [flyTo, setFlyTo] = useState<{ x: number; y: number; nonce: number } | null>(null);
  const [loading, setLoading] = useState(false);
  const [staticObjects, setStaticObjects] = useState<StaticWorldObject[]>([]);
  const [staticInfo, setStaticInfo] = useState<{ inView: number; truncated: boolean } | null>(null);
  const [staticSummary, setStaticSummary] = useState<StaticWorldSummary | null>(null);
  const flyNonce = useRef(0);

  // Categories this viewer is allowed to see at all. The summary omits the rest
  // entirely rather than flagging them, so an absent id is a withheld category.
  const visibleStaticIds = useMemo(
    () => new Set((staticSummary?.categories ?? []).map((c) => c.id)),
    [staticSummary]
  );

  // Any *visible* `static:` layer being on is what makes the viewport query worth
  // issuing. With them all off the map costs exactly what it did before this layer
  // existed.
  const staticWanted = LAYERS.some(
    (l) => l.group === 'static' && mapLayers[l.id] && visibleStaticIds.has(l.id.slice(7))
  );
  const staticWantedRef = useRef(staticWanted);
  staticWantedRef.current = staticWanted;

  const lastBox = useRef<{ minX: number; minY: number; maxX: number; maxY: number } | null>(null);
  const inFlight = useRef(0);

  /**
   * The per-category class selection to send, derived from what is *excluded*.
   *
   * A category with nothing excluded is omitted entirely rather than listing all
   * 17 of its classes: the backend treats an absent category as unfiltered, so
   * this keeps the URL short and means a class added by newer game data is
   * included by default rather than silently dropped.
   */
  const kindSelection = useMemo(() => {
    const selection: Record<string, string[]> = {};
    for (const category of staticSummary?.categories ?? []) {
      const off = staticKindsOff[category.id] ?? [];
      if (off.length === 0) continue;
      selection[category.id] = category.kinds
        .map((k) => k.cls)
        .filter((cls) => !off.includes(cls));
    }
    return selection;
  }, [staticSummary, staticKindsOff]);

  const kindsRef = useRef(kindSelection);
  kindsRef.current = kindSelection;

  /**
   * Fetch the static objects for a viewport.
   *
   * `inFlight` is a sequence number, not a boolean: panning quickly fires several
   * of these and the responses can land out of order, so an older answer must not
   * overwrite a newer one. Dropping the request instead would leave the map
   * showing the wrong area.
   */
  const loadStatic = useCallback(async (box: {
    minX: number; minY: number; maxX: number; maxY: number;
  }) => {
    lastBox.current = box;
    if (!staticWantedRef.current) {
      setStaticObjects([]);
      setStaticInfo(null);
      return;
    }
    const ticket = ++inFlight.current;
    try {
      const result = await getStaticWorldObjects({ ...box, kinds: kindsRef.current });
      if (ticket !== inFlight.current) return;
      setStaticObjects(result.points);
      setStaticInfo({ inView: result.inView, truncated: result.truncated });
    } catch {
      if (ticket !== inFlight.current) return;
      setStaticObjects([]);
      setStaticInfo(null);
    }
  }, []);

  // Re-fetch when a static layer is switched on, or the kind selection changes,
  // using the viewport already known.
  useEffect(() => {
    if (lastBox.current) void loadStatic(lastBox.current);
  }, [staticWanted, kindSelection, loadStatic]);

  useEffect(() => {
    getStaticWorldSummary().then(setStaticSummary).catch(() => setStaticSummary(null));
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    const [objects, points, found] = await Promise.allSettled([
      getMapObjects(),
      getFastTravelPoints(),
      // Discoveries may legitimately fail — a guest has no character, and the
      // policy may forbid the undiscovered half entirely. The map falls back to
      // the plain point list rather than losing the layer.
      getDiscoveries(),
    ]);
    setMapObjects(objects.status === 'fulfilled' ? objects.value : []);
    setFastTravel(points.status === 'fulfilled' ? points.value : []);
    setDiscoveries(found.status === 'fulfilled' ? found.value : null);
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
    acc.effigies = (discoveries?.effigies.points ?? []).filter((p) => transform.contains(p.x, p.y)).length;
    acc.players = onlinePlayers.filter((p) =>
      transform.contains(p.location_x, p.location_y)
    ).length;
    acc.bases = bases.filter((b) => transform.contains(b.x, b.y)).length;

    // Static layers count the world total from the summary, not what is loaded:
    // the loaded set is only ever the current viewport, so a per-viewport number
    // would read as "there are 14 ore nodes in Palworld" when zoomed in.
    for (const category of staticSummary?.categories ?? []) {
      acc[`static:${category.id}`] = category.count;
    }
    return acc;
  }, [mapObjects, fastTravel, discoveries, onlinePlayers, bases, transform, staticSummary]);

  /**
   * Save-derived layers, described in the same shape the static ones use, so
   * one panel serves both.
   *
   * These carry a `kind` too — the per-kind filter was only ever wired to the
   * pak-derived categories, so "show me only copper" worked on the game-file
   * ore layer and not on the chests your players have actually opened. Built
   * from what is in view, because unlike the static bundle there is no
   * world-total summary to quote.
   */
  const saveCategories = useMemo(() => {
    const byCategory = new Map<string, Map<string, number>>();
    // The friendly name the backend already resolved for each kind. A save calls
    // every minable node `DamagableRock…` regardless of what it yields, so
    // prettifying the class gave seventeen chips all reading "Damagable Rock"
    // while the popups beside them correctly said Copper, Coal and Sulfur. The
    // label has to come from the same place the popup gets it.
    const kindLabel = new Map<string, string>();
    for (const object of mapObjects) {
      if (!transform.contains(object.x, object.y)) continue;
      const kinds = byCategory.get(object.category) ?? new Map<string, number>();
      kinds.set(object.kind, (kinds.get(object.kind) ?? 0) + 1);
      byCategory.set(object.category, kinds);
      if (object.name && !kindLabel.has(object.kind)) {
        kindLabel.set(object.kind, object.name);
      }
    }
    // Effigies are their own layer and carry a kind from the extraction.
    const effigyKinds = new Map<string, number>();
    for (const point of discoveries?.effigies.points ?? []) {
      if (!transform.contains(point.x, point.y)) continue;
      const kind = point.kind || 'Effigy';
      effigyKinds.set(kind, (effigyKinds.get(kind) ?? 0) + 1);
    }
    if (effigyKinds.size) byCategory.set('effigies', effigyKinds);

    // Fast travel splits three ways — tower boss, watchtower, ordinary — and
    // the eight tower entrances were the ones people were actually hunting for.
    // Counted off `discoveries` when it is available so the numbers match the
    // markers drawn, which are the discovery-filtered set.
    const travelKinds = new Map<string, number>();
    const travelPoints = discoveries?.fastTravel.points ?? fastTravel;
    for (const point of travelPoints) {
      if (!transform.contains(point.x, point.y)) continue;
      const kind = point.kind ?? 'travel';
      travelKinds.set(kind, (travelKinds.get(kind) ?? 0) + 1);
    }
    if (travelKinds.size) byCategory.set('fastTravel', travelKinds);

    return [...byCategory.entries()].map(([id, kinds]) => ({
      id,
      label: id,
      count: [...kinds.values()].reduce((a, b) => a + b, 0),
      kinds: [...kinds.entries()]
        .map(([cls, count]) => ({ cls, count, label: kindLabel.get(cls) }))
        .sort((a, b) => b.count - a.count),
    }));
  }, [mapObjects, discoveries, fastTravel, transform]);

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

      {/* One button, one panel. This was 22 toggles across the top plus a stack
          of per-kind cards below, which competed with the map for attention and
          grew with every category added. */}
      <MapLayersPanel
        layers={LAYERS.filter(
          (l) => l.group !== 'static' || visibleStaticIds.has(l.id.slice(7))
        )}
        active={mapLayers}
        counts={counts}
        onToggle={toggleMapLayer}
        staticCategories={[...(staticSummary?.categories ?? []), ...saveCategories]}
        staticKindsOff={staticKindsOff}
        onToggleKind={toggleStaticKind}
        onSetKinds={setStaticKindsOff}
      />

      {/* What is loaded versus what exists. Saying what is *not* shown, rather
          than presenting a slice as the whole; zoom is the fix, so it is named.

          The stack of per-kind filter cards that used to sit here is gone. The
          layers panel above does the same job for **both** groups, and this
          block only ever rendered for the game-file categories — so ore had
          sub-filters in two places while chests from the save had them in one,
          which read as the save layers being less capable than they were. */}
      {staticWanted && (
        <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
          {staticInfo?.truncated
            ? `Showing ${staticObjects.length} of ${staticInfo.inView.toLocaleString()} static objects in view — zoom in to see the rest.`
            : `Showing ${staticObjects.length.toLocaleString()} static objects in view` +
              (staticSummary ? ` of ${staticSummary.objects.toLocaleString()} in the world.` : '.')}
          {' '}Positions come from the game files and are the same for everyone.
        </div>
      )}

      {/* Above the calibration notice: "this data may be from a different patch"
          qualifies every marker on the map, including the calibrated ones. */}
      <BuildBanner />

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
          discoveries={discoveries}
          staticObjects={staticObjects}
          layers={mapLayers}
        kindsOff={staticKindsOff}
          region={region}
          flyTo={flyTo}
          onMouseMove={(x, y) => setMouseCoords({ x, y })}
          onViewportChange={loadStatic}
        />
      </div>

      <p style={{ fontSize: 11, color: 'var(--text-muted)' }}>
        Coordinates match the in-game map. Fast-travel points come from bundled
        game data; everything else is read from your world.
      </p>
    </div>
  );
}

/**
 * `BP_PalMapObjectSpawner_RockCopper` -> `Rock Copper`.
 *
 * The same transform `map-inner` applies to popup titles. Duplicated rather than
 * shared because these are the only two callers and a one-function module for a
 * regex is not worth the import.
 */
