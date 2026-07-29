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
 * World Tree — derived from the game's own World Partition grid.
 *
 * WHAT CHANGED, AND WHY IT IS BETTER THAN WHAT WAS HERE BEFORE
 * -----------------------------------------------------------
 * The previous transform assumed the map framed the *fast-travel* bounding box
 * at ~82% of each axis. Both halves of that were wrong, and the game files say
 * so.
 *
 * `Pal-LinuxServer.pak` is unencrypted, and its index lists 9,978 World
 * Partition streaming cells for the main world, named
 * `MainGrid_L0_X<col>_Y<row>`. Those names *are* coordinates. The cell size is
 * measured, not guessed: at 25,600 world units, all **174 of 174** fast-travel
 * points land inside an occupied cell — 157/157 Palpagos and 17/17 World Tree.
 * 12,800 gets 66 and 51,200 gets 157, so the figure is unambiguous.
 *
 * That yields each landmass's true extent, which is the ground truth this
 * region never had. Projecting Palpagos' occupied-cell bounding box through its
 * own independently verified transform shows the map image frames the landmass
 * essentially edge to edge — 97.8% of the image on X, 99.0% on Y — not 82%, and
 * not the fast-travel box. Applying that same framing to the World Tree's cell
 * bounds moves markers by a mean of 80px on X and 196px on Y (max 467px)
 * against the old guess.
 *
 * STILL NOT `calibrated: true`, AND THAT IS DELIBERATE
 * ----------------------------------------------------
 * The extent is now real. One assumption remains: that the World Tree image
 * uses the same axis orientation as Palpagos (image X from world Y, image Y
 * from world X negated). Nothing has been checked against a known pixel
 * position on this landmass, so a flip or a transpose would still go unnoticed.
 * The moment anybody builds a base or opens a chest up there, the save yields
 * real positions and this can be *fitted* rather than derived — replace the
 * four constants and set the flag.
 *
 * Regenerate the bounds with `scripts/read-pak-index.py` after a game update;
 * a new landmass will show up as a new cell cluster.
 */
const WORLD_TREE_CELL_BOUNDS = { x1: 332800, x2: 691200, y1: -793600, y2: -486400 };

function worldTreeFromCellGrid(): RegionTransform {
  const { x1, x2, y1, y2 } = WORLD_TREE_CELL_BOUNDS;

  // world Y -> image X:  y1 -> 0,  y2 -> MAP_SIZE
  const imgXScale = MAP_SIZE / (y2 - y1);
  const imgXOffset = -y1 * imgXScale;

  // world X -> image Y, negated to match Palpagos' orientation:
  //   x1 -> MAP_SIZE,  x2 -> 0
  const imgYScale = -MAP_SIZE / (x2 - x1);
  const imgYOffset = MAP_SIZE - x1 * imgYScale;

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
      'World Tree positions are derived from the game’s streaming-cell grid, so the ' +
      'landmass extent is exact — but the image orientation has never been checked ' +
      'against a known point up here, so treat placement as close rather than certain.',
  };
}

const WORLD_TREE = worldTreeFromCellGrid();

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
