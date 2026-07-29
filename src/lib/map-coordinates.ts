/**
 * Palworld coordinate conversion.
 *
 * The game stores Unreal world coordinates in the save, but the map players
 * actually read uses a different system — and crucially the axes are SWAPPED:
 * in-game map X is derived from world Y, and map Y from world X.
 *
 * TWO MAPS, NOT ONE
 * -----------------
 * An earlier version of this file assumed Palworld 1.0's two landmasses shared
 * one image. They do not. Checking all 174 fast-travel points from the bundled
 * game data against the fitted transform:
 *
 *   157/157 Palpagos points land inside the map image
 *     0/17  World Tree points do (they fall at negative pixel coordinates)
 *
 * So the World Tree is a separate map with its own framing, exactly as it is
 * in-game, and each region needs its own transform.
 *
 * That check is also the strongest validation the Palpagos transform has: those
 * 157 points were not used to fit it, and every one lands on the image.
 */

export type MapRegion = 'palpagos' | 'worldtree';

interface RegionTransform {
  id: MapRegion;
  label: string;
  image: string;
  /** world Y -> image X, world X -> image Y (the axes really do swap). */
  imgXScale: number;
  imgXOffset: number;
  imgYScale: number;
  imgYOffset: number;
  /** World-space test: does a point belong to this region? */
  contains: (worldX: number, worldY: number) => boolean;
  /**
   * False when the transform is inferred rather than fitted to known points.
   * The UI says so rather than presenting guesses as fact.
   */
  calibrated: boolean;
  note?: string;
}

/** Leaflet coordinate space. The images are 8192px; Leaflet scales them. */
const MAP_SIZE = 4096;

/**
 * World X above this sits on the World Tree landmass.
 *
 * The two are far apart in world space — Palpagos fast-travel points top out at
 * x = 192,506 and World Tree points start at x = 405,905 — so anything in the
 * gap is unambiguous.
 */
const WORLD_TREE_X_THRESHOLD = 300000;

const PALPAGOS: RegionTransform = {
  id: 'palpagos',
  label: 'Palpagos Islands',
  image: '/maps/palpagos.webp',
  // Least-squares fit over the palcalc reference samples (world <-> in-game <->
  // image triples from known boss locations). Residuals: X +/-0.34, Y +/-0.46
  // in-game map units, ~459 world units per map unit. Independently confirmed
  // by all 157 Palpagos fast-travel points landing on the image.
  imgXScale: 0.0028463649168173903,
  imgXOffset: 2045.4249901028509,
  imgYScale: -0.0028275391990127056,
  imgYOffset: 987.5352466783819,
  contains: (worldX) => worldX <= WORLD_TREE_X_THRESHOLD,
  calibrated: true,
};

/*
 * World Tree — PROVISIONAL, and deliberately labelled as such.
 *
 * There is no ground truth to fit against yet:
 *   - The reference save has zero placed objects on this landmass, so the save
 *     offers no known positions.
 *   - The 17 fast-travel points give world coordinates but no pixel positions.
 *   - Detecting land in the texture is too weak to optimise against: sampling
 *     the 157 *known-correct* Palpagos points found 36% of them "ocean-blue"
 *     versus 58% of random pixels. That signal cannot pin down four parameters
 *     from 17 points.
 *
 * So this is derived from one stated assumption: the World Tree map frames its
 * landmass the way the Palpagos map frames its own, with the fast-travel
 * bounding box centred and covering ~82% of each axis (measured on Palpagos).
 * Markers will be in roughly the right relative arrangement, but the absolute
 * placement is unverified and may be off by a noticeable margin.
 *
 * TO FIX PROPERLY: the moment anybody builds a base or opens a chest on the
 * World Tree, the save yields real positions there and this can be fitted the
 * same way Palpagos was. Replace the four constants below and set
 * `calibrated: true`. Nothing else needs to change.
 */
const WORLD_TREE_FT_BOUNDS = { x1: 405905, x2: 628792, y1: -757915, y2: -510663 };
const FRAMING = 0.82;

function provisionalWorldTree(): RegionTransform {
  const spanWorldX = WORLD_TREE_FT_BOUNDS.x2 - WORLD_TREE_FT_BOUNDS.x1;
  const spanWorldY = WORLD_TREE_FT_BOUNDS.y2 - WORLD_TREE_FT_BOUNDS.y1;
  const usable = MAP_SIZE * FRAMING;
  const margin = (MAP_SIZE - usable) / 2;

  // world Y -> image X:  y1 -> margin,  y2 -> margin + usable
  const imgXScale = usable / spanWorldY;
  const imgXOffset = margin - WORLD_TREE_FT_BOUNDS.y1 * imgXScale;

  // world X -> image Y, negated to match Palpagos' orientation:
  //   x1 -> margin + usable,  x2 -> margin
  const imgYScale = -usable / spanWorldX;
  const imgYOffset = margin + usable - WORLD_TREE_FT_BOUNDS.x1 * imgYScale;

  return {
    id: 'worldtree',
    label: 'World Tree',
    image: '/maps/worldtree.webp',
    imgXScale,
    imgXOffset,
    imgYScale,
    imgYOffset,
    contains: (worldX) => worldX > WORLD_TREE_X_THRESHOLD,
    calibrated: false,
    note:
      'Positions on the World Tree map are approximate. The transform is inferred ' +
      'from the landmass extent, not fitted to known points — no save data exists ' +
      'for this region yet.',
  };
}

const WORLD_TREE = provisionalWorldTree();

export const MAP_REGIONS: RegionTransform[] = [PALPAGOS, WORLD_TREE];

export function regionFor(worldX: number, worldY: number): RegionTransform {
  return MAP_REGIONS.find((r) => r.contains(worldX, worldY)) ?? PALPAGOS;
}

export function getRegion(id: MapRegion): RegionTransform {
  return MAP_REGIONS.find((r) => r.id === id) ?? PALPAGOS;
}

// ─── In-game map coordinates ─────────────────────────────
// One continuous scale across both landmasses — this is what the game shows the
// player, and what they type when sharing a location.
const MAP_X_SCALE = 0.002179136697594164;
const MAP_X_OFFSET = -344.02696538543;
const MAP_Y_SCALE = 0.0021787337566978138;
const MAP_Y_OFFSET = 270.1851591908168;

/**
 * World coordinates to the numbers shown on the in-game map.
 * Use this for anything a player reads or types (e.g. "-134, -94").
 */
export function worldToGameMap(worldX: number, worldY: number): { x: number; y: number } {
  return {
    x: Math.round(worldY * MAP_X_SCALE + MAP_X_OFFSET),
    y: Math.round(worldX * MAP_Y_SCALE + MAP_Y_OFFSET),
  };
}

/** In-game map coordinates back to world coordinates. */
export function gameMapToWorld(mapX: number, mapY: number): { x: number; y: number } {
  return {
    x: Math.round((mapY - MAP_Y_OFFSET) / MAP_Y_SCALE),
    y: Math.round((mapX - MAP_X_OFFSET) / MAP_X_SCALE),
  };
}

// ─── Leaflet placement ───────────────────────────────────

/**
 * World coordinates to Leaflet [lat, lng] on a given region's map.
 *
 * Image pixels have their origin at the top-left with y increasing downward,
 * while CRS.Simple puts [0,0] at the bottom-left, so the y axis is flipped.
 */
export function worldToMap(
  worldX: number,
  worldY: number,
  region: MapRegion = 'palpagos'
): [number, number] {
  const t = getRegion(region);
  const imageX = worldY * t.imgXScale + t.imgXOffset;
  const imageY = worldX * t.imgYScale + t.imgYOffset;
  return [MAP_SIZE - imageY, imageX];
}

/** Leaflet [lat, lng] back to world coordinates on a given region's map. */
export function mapToWorld(
  lat: number,
  lng: number,
  region: MapRegion = 'palpagos'
): { x: number; y: number } {
  const t = getRegion(region);
  const imageY = MAP_SIZE - lat;
  return {
    x: Math.round((imageY - t.imgYOffset) / t.imgYScale),
    y: Math.round((lng - t.imgXOffset) / t.imgXScale),
  };
}

/** Format world coordinates as the in-game map values players recognise. */
export function formatCoords(x: number, y: number, z?: number): string {
  const map = worldToGameMap(x, y);
  const base = `${map.x}, ${map.y}`;
  return z !== undefined ? `${base}  (alt ${Math.round(z / 100)}m)` : base;
}

/** Distance between two world points, in metres (world units are centimetres). */
export function worldDistance(x1: number, y1: number, x2: number, y2: number): number {
  return Math.hypot(x2 - x1, y2 - y1) / 100;
}

export { MAP_SIZE, WORLD_TREE_X_THRESHOLD };
export type { RegionTransform };
