/**
 * A distinct colour per object *kind*, not just per category.
 *
 * "Ore" is 17 different rocks and "chests" is 10 biome variants, all drawn in
 * one grey and one gold. Someone hunting coal could filter the rest out, but
 * with everything left on the map said nothing about which rock was which —
 * and the filter chips were equally uniform.
 *
 * Curated where it matters, hashed where it does not. The named entries are the
 * ones with an obvious real-world colour (copper is orange, coal is black,
 * sulfur is yellow), because a hash that made coal pink would be worse than
 * grey. Everything else gets a stable hue from its own name, which is enough to
 * tell two kinds apart even when neither colour "means" anything.
 */

/** Substring -> colour. First match wins, so order longest-first where they overlap. */
const NAMED: [string, string][] = [
  // ─── Ore and gathering ───
  ['RockCopper', '#b87333'],
  ['RockCoal', '#3a3f45'],
  ['RockIron', '#9aa3ab'],
  ['Sulfur', '#d9c94a'],
  ['Quartz', '#d8d8e0'],
  ['PalCrystal', '#7fb8d9'],
  ['Crystal', '#9ec9e8'],
  ['CaveMushroom', '#b06a8a'],
  ['SmallStone', '#8a8378'],
  ['RockStone', '#7d766c'],
  ['log', '#7a5a3a'],
  ['Ice', '#a8d8e8'],
  ['Sand', '#d9c9a0'],

  // ─── Fast travel, which is three different things in one list ───
  // `watchtower` must come before `tower`: matching is by substring and
  // first-hit-wins, so the other order paints all 22 watchtowers in the tower
  // boss's red — which is precisely the confusion this split exists to end.
  ['watchtower', '#8fc4e0'],
  ['tower', '#c25757'],
  ['travel', '#e0c060'],

  // ─── Chests, which vary by biome rather than contents ───
  ['DarkIslandDrop', '#8d6fb0'],
  ['WorldTreeDrop', '#d9b44a'],
  ['SkyIslandDrop', '#8fc4e0'],
  ['DessertDrop', '#d6b678'],
  ['volcanoDrop', '#c25a3a'],
  ['snowDrop', '#cfe2ea'],
  ['grassDrop', '#7fa05b'],
];

/**
 * Deterministic hue from a string.
 *
 * FNV-1a rather than a naive sum: short class names differing by one character
 * (`RockStone` / `RockStone5`) must not land on adjacent hues, and a sum does
 * exactly that. Saturation and lightness are fixed so nothing comes out muddy
 * against a dark map or invisible against a light one.
 */
function hashedColor(value: string): string {
  let hash = 0x811c9dc5;
  for (let i = 0; i < value.length; i += 1) {
    hash ^= value.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return `hsl(${hash % 360}, 45%, 62%)`;
}

/**
 * Colour for one object kind.
 *
 * `fallback` is the category's own colour, used when the kind is empty — a
 * marker with no class should look like its category rather than like a
 * hash of the empty string.
 */
export function kindColor(cls: string | null | undefined, fallback: string): string {
  if (!cls) return fallback;
  for (const [needle, color] of NAMED) {
    if (cls.includes(needle)) return color;
  }
  return hashedColor(cls);
}


/**
 * Shape per *category*, colour per *kind*.
 *
 * Two dimensions because there are two questions. Colour already separates the
 * 17 ore types from each other; shape separates ore from chests from effigies,
 * which colour alone cannot do once every kind has its own hue. It also survives
 * the case colour does not: a colour-blind viewer, or a marker sitting on
 * similarly-coloured terrain.
 *
 * Leaflet's canvas renderer only draws circles, so anything else is an SVG path
 * on a `divIcon`. That is heavier per marker, which is why it is reserved for
 * the sparse categories — the 24,359 ore nodes stay circles, where the sheer
 * density means shape would be indistinguishable anyway.
 */
export type MarkerShape = 'circle' | 'square' | 'triangle' | 'diamond';

const SHAPES: Record<string, MarkerShape> = {
  // Static, pak-derived
  treasure: 'square',
  dungeon: 'triangle',
  oilrig: 'diamond',
  effigy: 'triangle',
  // Save-derived
  chest: 'square',
  oilrigChest: 'diamond',
  egg: 'diamond',
  statue: 'triangle',
  effigies: 'triangle',
  // Bundled game data. Diamond because that is the marker on the map, and a
  // chip that does not match its marker is worse than no chip.
  fastTravel: 'diamond',
  npc: 'square',
  palspawner: 'circle',
};

export function markerShape(category: string): MarkerShape {
  return SHAPES[category] ?? 'circle';
}

/**
 * An inline SVG marker of the given shape, sized in CSS pixels.
 *
 * Returns markup rather than an element so it can go straight into a Leaflet
 * `divIcon`, which takes an HTML string.
 */
export function shapeSvg(shape: MarkerShape, size: number, color: string): string {
  const half = size / 2;
  const stroke = 'rgba(0,0,0,.55)';
  const common = `fill="${color}" stroke="${stroke}" stroke-width="1"`;
  const body =
    shape === 'square'
      ? `<rect x="1" y="1" width="${size - 2}" height="${size - 2}" rx="1" ${common}/>`
      : shape === 'triangle'
        ? `<polygon points="${half},1 ${size - 1},${size - 1} 1,${size - 1}" ${common}/>`
        : shape === 'diamond'
          ? `<polygon points="${half},1 ${size - 1},${half} ${half},${size - 1} 1,${half}" ${common}/>`
          : `<circle cx="${half}" cy="${half}" r="${half - 1}" ${common}/>`;
  return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" style="display:block">${body}</svg>`;
}
