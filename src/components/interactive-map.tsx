'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import MapLayersPanel from '@/components/map-layers-panel';
import { useDashboardStore } from '@/lib/store';
import { formatCoords, getRegion, MAP_REGIONS, type MapRegion } from '@/lib/map-coordinates';
import {
  getMapObjects, getFastTravelPoints, getDiscoveries, getEffigyPoints,
  getStaticWorldObjects, getStaticWorldSummary, getBossSpawners, getNpcPlacements,
  type BossSpawner,
  getGuildMarkers,
  getRespawns,
  type RespawnPin,
  type GuildMarker,
} from '@/lib/save-api';
import { Crosshair, RefreshCw, Search, Info } from 'lucide-react';
import dynamic from 'next/dynamic';
import BuildBanner from './build-banner';
import type {
  MapObject, FastTravelPoint, Discoveries, DiscoveryPoint,
  StaticWorldObject, StaticWorldSummary, NpcPlacement,
} from '@/lib/types';
import { asArray } from '@/lib/arrays';
import { t, tl } from '@/lib/chrome';

const MapComponent = dynamic(() => import('./map-inner'), { ssr: false });

/**
 * Layer toggles.
 *
 * `fastTravel` is the one layer that does not come from the save — fast-travel
 * statues are static level actors, so their positions ship with the dashboard as
 * bundled game data. Everything else is read out of the world.
 */
const LAYERS: { id: string; label: string; color: string; group: 'live' | 'discovery' | 'world' | 'static' | 'npc' | 'base' }[] = [
  { id: 'players', label: tl('Players'), color: '#5b9dd9', group: 'live' },
  { id: 'bases', label: tl('Bases'), color: '#c9973f', group: 'live' },

  // Their own group, because they are neither of the other two and sat under
  // "From the save" claiming to be something they are not. Positions ship with
  // the dashboard (extracted from the pak); the save contributes only which ones
  // *you* have found. The comment above said exactly this while the group label
  // said otherwise.
  //
  // The group also maps one-to-one onto `discoveryCategoryVisibility`, so what
  // an operator sets on the Access tab and what a player sees here are named the
  // same thing.
  { id: 'fastTravel', label: tl('Fast travel'), color: '#e0c060', group: 'discovery' },
  { id: 'effigies', label: tl('Effigies'), color: '#8d84c7', group: 'discovery' },
  { id: 'bosses', label: tl('Field bosses'), color: '#d4574e', group: 'discovery' },


  // "From the save", which is exactly what these are — not something the
  // world contains and you discover, but something a player wrote into it.
  // The server only ever sends you your own guild's.
  { id: 'guildMarkers', label: tl('Guild markers'), color: '#4ea8d4', group: 'world' },
  // Save-derived: nodes with a RUNNING respawn clock. A harvested node whose
  // timer is already due respawns on approach and is deliberately not pinned.
  { id: 'respawns', label: tl('Respawning nodes'), color: '#7fd48f', group: 'world' },
  { id: 'chest', label: tl('Chests'), color: '#c9973f', group: 'world' },
  { id: 'oreNode', label: tl('Ore nodes'), color: '#8a8378', group: 'world' },
  { id: 'oilrigChest', label: tl('Oil rig'), color: '#d97757', group: 'world' },
  { id: 'fishingJunk', label: tl('Fishing junk'), color: '#5f6b73', group: 'world' },

  // Pak-derived, and a different thing from the save-derived layers above: these
  // are every node the game ships, not the ones a save has state for. Namespaced
  // `static:` so the two can be toggled independently and never collide.
  { id: 'static:ore', label: tl('All ore'), color: '#8a8378', group: 'static' },
  { id: 'static:treasure', label: tl('All chests'), color: '#c9973f', group: 'static' },
  { id: 'static:fishing', label: tl('Fishing spots'), color: '#5f6b73', group: 'static' },
  { id: 'static:palegg', label: tl('Wild Pal eggs'), color: '#d98cc4', group: 'static' },
  { id: 'static:oilrig', label: tl('Oil fields'), color: '#d97757', group: 'static' },
  // Extracted all along and never given a toggle, so 2,163 dungeon objects and
  // 13,851 spawners sat in the bundle unreachable. A category the backend
  // withholds still gets no toggle — `visibleStaticIds` filters this list.
  { id: 'static:dungeon', label: tl('Dungeons'), color: '#9a6fb0', group: 'static' },
  { id: 'static:palspawner', label: tl('Pal spawns'), color: '#7fa05b', group: 'static' },
  // NAMED NPC LAYERS, one per role. These replace the anonymous
  // `static:npc` toggle: 141 of those 220 points were the generic class
  // `BP_MonoNPCSpawner`, so the layer could say "someone stands here" and never
  // who. A spawner actor's tagged properties are readable in the server pak, so
  // it now says "Black Marketeer, level 45".
  //
  // The role split is a NAME RULE — no game table carries a role — and it fails
  // safe: anything unrecognised lands in "Other NPCs".
  { id: 'npc:merchant', label: tl('Merchants & traders'), color: '#e0c060', group: 'npc' },
  { id: 'npc:villager', label: tl('Villagers'), color: '#7fa05b', group: 'npc' },
  { id: 'npc:police', label: tl('PIDF & law'), color: '#5b9dd9', group: 'npc' },
  { id: 'npc:hunter', label: tl('Hunters & raiders'), color: '#d4574e', group: 'npc' },
  { id: 'npc:scholar', label: tl('Scholars & specialists'), color: '#9a6fb0', group: 'npc' },
  { id: 'npc:quest', label: tl('Quest & event NPCs'), color: '#d98cc4', group: 'npc' },
  { id: 'npc:npc', label: tl('Other NPCs'), color: '#c9a227', group: 'npc' },
  // The alpha Pals that drop Ancient Technology Points. 99 in the world, named
  // and drawn with the Pal's own artwork — they were previously indistinguishable
  // from the other 13,851 spawn points.
  { id: 'static:fieldboss', label: tl('Field bosses'), color: '#d14b4b', group: 'static' },
  // Found by a coverage check over every placeable class in the pak, after a
  // community map showed content this one did not. Extracted from the same pak
  // as everything else rather than copied from anyone's marker data.
  { id: 'static:skillfruit', label: tl('Skill & kinship fruit'), color: '#d98cc4', group: 'static' },
  { id: 'static:lotus', label: tl('Stat lotuses'), color: '#7fd4c1', group: 'static' },
  { id: 'static:junk', label: tl('Junk piles'), color: '#8a7a5f', group: 'static' },
  { id: 'static:collectible', label: tl('Coins & pots'), color: '#e0c060', group: 'static' },
  { id: 'static:supply', label: tl('Supply drops'), color: '#5b9dd9', group: 'static' },

  { id: 'palbox', label: tl('Palboxes'), color: '#5b9dd9', group: 'base' },
  // `breeding` used to be the Ranch. The Breeding Farm matched no category at
  // all, so none were ever drawn — see `_POI_CATEGORIES` in the parser.
  { id: 'breeding', label: tl('Breeding farms'), color: '#8d84c7', group: 'base' },
  { id: 'ranch', label: tl('Ranches'), color: '#b58cc7', group: 'base' },
  { id: 'statue', label: tl('Statues'), color: '#4d9e75', group: 'base' },
  { id: 'crafting', label: tl('Crafting'), color: '#a1a7b0', group: 'base' },
  { id: 'production', label: tl('Production'), color: '#6d747e', group: 'base' },
  { id: 'farm', label: tl('Farms'), color: '#7fa05b', group: 'base' },
  { id: 'storage', label: tl('Storage'), color: '#c25757', group: 'base' },
  { id: 'defense', label: tl('Defense'), color: '#b0553f', group: 'base' },
];

export default function InteractiveMap() {
  const {
    onlinePlayers, bases, saveDataError, mapLayers, toggleMapLayer,
    staticKindsOff, toggleStaticKind, setStaticKindsOff,
    hideCollected, setHideCollected,
  } = useDashboardStore();
  const [mouseCoords, setMouseCoords] = useState<{ x: number; y: number } | null>(null);
  const [mapObjects, setMapObjects] = useState<MapObject[]>([]);
  const [fastTravel, setFastTravel] = useState<FastTravelPoint[]>([]);
  const [discoveries, setDiscoveries] = useState<Discoveries | null>(null);
  const [effigies, setEffigies] = useState<DiscoveryPoint[]>([]);
  const [bosses, setBosses] = useState<BossSpawner[]>([]);
  const [npcs, setNpcs] = useState<NpcPlacement[]>([]);
  const [guildMarkers, setGuildMarkers] = useState<GuildMarker[]>([]);
  const [respawns, setRespawns] = useState<RespawnPin[]>([]);
  // Not the same as `guildMarkers.length === 0`: an empty list because your
  // guild has placed none, and an empty list because you are in no guild, are
  // different answers and only the second needs saying.
  const [markerScope, setMarkerScope] = useState<'all' | 'guild' | 'none'>('none');
  const [discoveryError, setDiscoveryError] = useState<string | null>(null);
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
    () => new Set(asArray(staticSummary?.categories, 'map categories').map((c) => c.id)),
    [staticSummary]
  );

  // Any *visible* `static:` layer being on is what makes the viewport query worth
  // issuing. With them all off the map costs exactly what it did before this layer
  // existed.
  const staticWanted = LAYERS.some(
    (l) => l.group === 'static' && mapLayers[l.id] && visibleStaticIds.has(l.id.slice(7))
  );
  const staticWantedRef = useRef(staticWanted);
  // Written in an effect, not during render. The "latest ref" pattern is
  // right — this value is read inside a fetch callback that must not be a
  // dependency — but assigning during render is a real hazard under
  // concurrent rendering, where a render can be thrown away and the ref
  // would keep a value that was never committed.
  useEffect(() => {
    staticWantedRef.current = staticWanted;
  }, [staticWanted]);

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
  useEffect(() => {
    kindsRef.current = kindSelection;
  }, [kindSelection]);

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
    const [objects, points, found, relics, fieldBosses, people, pins, regrowing] = await Promise.allSettled([
      getMapObjects(),
      getFastTravelPoints(),
      // Discoveries may legitimately fail — a guest has no character, and the
      // policy may forbid the undiscovered half entirely. The map falls back to
      // the plain point lists rather than losing the layers.
      getDiscoveries(),
      // The effigy half of that fallback. It did not exist, so while fast travel
      // survived a failed /discoveries call, effigies silently disappeared — the
      // layer toggle stayed on and drew nothing, with no error anywhere.
      getEffigyPoints(),
      // Independent of the discovery pair above: this layer has no per-player
      // state, so it neither needs nor has a fallback — it either loads or the
      // toggle draws nothing, which the empty-layer note below already covers.
      getBossSpawners(),
      // Independent again, and settled rather than caught: an empty NPC list is
      // a legitimate answer, so a `.catch(() => [])` would hide a failed fetch
      // behind one.
      getNpcPlacements(),
      // Guild-scoped server-side. Settled, not caught: an empty list is a
      // legitimate answer here more often than anywhere else on this map, so a
      // swallowed failure would be invisible.
      getGuildMarkers(),
      // Save-derived; a world with no parse 503s, and that is a quiet layer
      // rather than an error — the toggle simply draws nothing.
      getRespawns(),
    ]);
    setMapObjects(objects.status === 'fulfilled' ? objects.value : []);
    setFastTravel(points.status === 'fulfilled' ? points.value : []);
    setDiscoveries(found.status === 'fulfilled' ? found.value : null);
    setEffigies(relics.status === 'fulfilled' ? relics.value : []);
    setBosses(fieldBosses.status === 'fulfilled' ? fieldBosses.value : []);
    setNpcs(people.status === 'fulfilled' ? people.value.placements : []);
    setGuildMarkers(pins.status === 'fulfilled' ? pins.value.points : []);
    setRespawns(regrowing.status === 'fulfilled' ? regrowing.value.pins : []);
    setMarkerScope(pins.status === 'fulfilled' ? pins.value.scope : 'none');
    // A layer that is switched on and empty is indistinguishable from a layer
    // that failed to load, which is how "effigies not showing" went undiagnosed.
    // Only reported when the fallback failed too — one endpoint being down while
    // the other answers is a degradation, not an outage.
    setDiscoveryError(
      found.status === 'rejected' && relics.status === 'rejected'
        ? relics.reason instanceof Error
          ? relics.reason.message
          : 'Discovery data could not be loaded'
        : null
    );
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
    // Same fallback the markers use, so the chip count and what is drawn cannot
    // disagree — a "0" beside a layer full of markers reads as a broken filter.
    acc.effigies = (discoveries?.effigies.points ?? effigies)
      .filter((p) => transform.contains(p.x, p.y)).length;
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
  }, [mapObjects, fastTravel, discoveries, effigies, onlinePlayers, bases, transform, staticSummary]);

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
    // Their label comes from the backend for the same reason ore's does: the
    // raw class is a game-file name, and `prettyClass` would render
    // `BP_LevelObject_Relic_SheepBall` as "Relic Sheep Ball" rather than
    // "Lamball Effigy".
    const effigyKinds = new Map<string, number>();
    for (const point of discoveries?.effigies.points ?? effigies) {
      if (!transform.contains(point.x, point.y)) continue;
      const kind = point.kind || 'Effigy';
      effigyKinds.set(kind, (effigyKinds.get(kind) ?? 0) + 1);
      if (point.kindName && !kindLabel.has(kind)) kindLabel.set(kind, point.kindName);
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
  }, [mapObjects, discoveries, effigies, fastTravel, transform]);

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
            placeholder={t('Find a fast travel point or base…')}
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
        staticCategories={[...asArray(staticSummary?.categories, 'map categories'), ...saveCategories]}
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

      {discoveryError && (
        <div className="notice notice-warn" style={{ fontSize: 12 }}>
          <Info size={13} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 5 }} />
          <strong>{t('Fast-travel and effigy layers unavailable.')}</strong>{' '}
          {discoveryError}
        </div>
      )}

      {/* An empty guild-marker layer has two completely different causes, and
          the toggle looks identical for both: your guild has placed no pins, or
          your account is not linked to a character so the server has no guild to
          scope to. Only the second needs saying, and saying it is the difference
          between "nothing to show" and "the dashboard is broken" — the same
          distinction the effigy fallback and the ban list both had to make. */}
      {mapLayers.guildMarkers && markerScope === 'none' && (
        <div className="notice" style={{ fontSize: 12 }}>
          <Info size={13} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 5 }} />
          Guild markers are shared with guild members only, and this account is
          not linked to a character in any guild — so there are none to show
          rather than none placed.
        </div>
      )}

      {/* COMPLETION MODE.
          Sits beside the layers button rather than inside the panel because it
          is not a layer — it changes what the collectable layers *mean*, from
          "here is everything" to "here is what is left".

          Disabled without `discoveries`, and it says why. That route needs a
          signed-in account with a linked character, so a guest genuinely cannot
          have this; a checkbox that silently did nothing would read as broken. */}
      <label
        style={{
          display: 'flex', alignItems: 'center', gap: 7, fontSize: 12,
          color: discoveries ? 'var(--text-secondary)' : 'var(--text-muted)',
          cursor: discoveries ? 'pointer' : 'not-allowed',
        }}
        title={
          discoveries
            ? 'Show only effigies and fast-travel points you have not collected yet.'
            : 'Needs your collection progress, which requires a signed-in account ' +
              'linked to a character.'
        }
      >
        <input
          type="checkbox"
          checked={hideCollected}
          disabled={!discoveries}
          onChange={(e) => setHideCollected(e.target.checked)}
        />
        <span>
          Completion mode — hide what I have already collected
          {!discoveries && ' (unavailable: no collection progress)'}
        </span>
      </label>

      {hideCollected && discoveries && (
        <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
          {(() => {
            const left = (n: DiscoveryPoint[]) =>
              n.filter((p) => transform.contains(p.x, p.y) && p.discovered !== true).length;
            const effigiesLeft = left(discoveries.effigies.points);
            const travelLeft = left(discoveries.fastTravel.points);
            return (
              <>
                <strong>{effigiesLeft}</strong> effigies and{' '}
                <strong>{travelLeft}</strong> fast-travel points left on{' '}
                {transform.label}.
                {/* Two ways this count can mislead, and both are the server's
                    doing rather than the player's, so both are named. */}
                {!discoveries.showsUndiscovered && (
                  <> This server does not send undiscovered locations, so these
                    are only the ones it chose to reveal — treat them as a floor.</>
                )}
                {!discoveries.linkedToPlayer && (
                  <> Your account is not linked to a character, so nothing reads
                    as collected and everything is still showing.</>
                )}
              </>
            );
          })()}
        </div>
      )}

      {saveDataError && (
        <div className="notice notice-warn" style={{ fontSize: 12 }}>
          <Info size={13} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 5 }} />
          <strong>{t('Base layer unavailable.')}</strong> {saveDataError}
        </div>
      )}

      {!mapObjects.length && !loading && (
        <div className="notice" style={{ fontSize: 12 }}>
          No map objects loaded. Save data is parsed on demand — press{' '}
          <strong>{t('Refresh')}</strong> on the Overview tab to parse the world, then
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
          effigies={effigies}
          bosses={bosses}
          npcs={npcs}
          guildMarkers={guildMarkers}
          respawns={respawns}
          hideCollected={hideCollected}
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
