/**
 * Palworld coordinate conversion.
 *
 * The game stores Unreal world coordinates in the save, but the map players
 * actually read uses a different system — and crucially the axes are SWAPPED:
 * in-game map X is derived from world Y, and map Y from world X.
 *
 * The previous implementation assumed a symmetric ±800000 world mapped straight
 * onto the image, which is wrong on both the axis order and the offsets, so
 * every marker landed in the wrong place.
 *
 * These constants are a least-squares fit over the reference samples published
 * by the palcalc project (world ↔ in-game ↔ image coordinate triples taken from
 * known boss locations). Residuals on the fit:
 *   map X  ±0.34   map Y  ±0.46   (in-game map units)
 * The scale works out at ~459 world units per map unit, matching the value the
 * community uses.
 */

// ─── Fitted transform: world -> in-game map coordinates ──
const MAP_X_SCALE = 0.002179136697594164;   // applied to world Y
const MAP_X_OFFSET = -344.02696538543;
const MAP_Y_SCALE = 0.0021787337566978138;  // applied to world X
const MAP_Y_OFFSET = 270.1851591908168;

// ─── Fitted transform: world -> map image pixels ─────────
// Calibrated against the standard 4096px Palworld map image.
const IMG_X_SCALE = 0.0028463649168173903;  // applied to world Y
const IMG_X_OFFSET = 2045.4249901028509;
const IMG_Y_SCALE = -0.0028275391990127056; // applied to world X
const IMG_Y_OFFSET = 987.5352466783819;

/** Size of the Leaflet coordinate space, matching the reference map image. */
const MAP_SIZE = 4096;

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

/**
 * World coordinates to Leaflet [lat, lng] for a CRS.Simple map.
 *
 * Image pixels have their origin at the top-left with y increasing downward,
 * while CRS.Simple puts [0,0] at the bottom-left, so the y axis is flipped here.
 */
export function worldToMap(worldX: number, worldY: number): [number, number] {
  const imageX = worldY * IMG_X_SCALE + IMG_X_OFFSET;
  const imageY = worldX * IMG_Y_SCALE + IMG_Y_OFFSET;
  return [MAP_SIZE - imageY, imageX];
}

/** Leaflet [lat, lng] back to world coordinates. */
export function mapToWorld(lat: number, lng: number): { x: number; y: number } {
  const imageY = MAP_SIZE - lat;
  return {
    x: Math.round((imageY - IMG_Y_OFFSET) / IMG_Y_SCALE),
    y: Math.round((lng - IMG_X_OFFSET) / IMG_X_SCALE),
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

export { MAP_SIZE };
