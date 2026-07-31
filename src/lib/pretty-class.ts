/**
 * Blueprint class name -> something readable.
 *
 *     BP_PalMapObjectSpawner_RockCopper  ->  Rock Copper
 *     BP_MapObject_FishingJunkSpot_01    ->  Fishing Junk Spot 01
 *
 * Shared because three places render these: the map's popups, the layer panel's
 * per-kind chips, and the object list. They were drifting copies — this is not
 * complex enough to justify one, let alone three.
 *
 * Falls back to the raw class rather than an empty string: an unrecognised
 * prefix should read as an odd name, not as a blank row.
 */
/**
 * Kinds that are already plain words, not Blueprint class names.
 *
 * The save- and bundle-derived layers reuse the same per-kind chips as the
 * pak-derived ones, but their "class" is a short tag we assign. Running `tower`
 * through the Blueprint stripper leaves it unchanged, which is fine — it is the
 * capitalisation and the "boss" that need saying.
 */
const PLAIN: Record<string, string> = {
  tower: 'Tower boss',
  watchtower: 'Watchtower',
  travel: 'Fast travel',
};

export function prettyClass(cls: string): string {
  if (PLAIN[cls]) return PLAIN[cls];
  return (
    cls
      .replace(/^BP_(PalMapObjectSpawnerTreasureBox|PalMapObjectSpawner|MapObject|LevelObject|PalSpawner|Dungeon)_?/, '')
      .replace(/^VisibleContent_?/, '')
      .replace(/^Sheets_?/, '')
      .replace(/_/g, ' ')
      .replace(/([a-z])([A-Z])/g, '$1 $2')
      .trim() || cls
  );
}
