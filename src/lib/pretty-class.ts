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
export function prettyClass(cls: string): string {
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
