/**
 * Game icons, installed by `scripts/install-icons.py` into `public/icons/`.
 *
 * **There is no lookup table, deliberately.** `gamedata.json.gz` already records
 * each item's and Pal's icon path, `describe_item()` / `describe_pal()` already
 * return it, and the installer preserves the archive's filenames — so an API
 * response carries a path that resolves directly.
 *
 * A first version renamed the files to the ids the API speaks and shipped a
 * lowercased manifest to resolve them case-insensitively. It matched **0 of
 * 2,466** items, because item icons are named after their texture
 * (`T_itemicon_Material_AIcore`) and nothing turns that into `AIcore`. Deriving
 * a mapping the data already contained created a second source of truth that
 * disagreed with the first. Preserving filenames took coverage to 99.6%.
 *
 * So this module holds one thing: whether the icons are installed at all.
 * They are optional, and a clone that skipped the installer must render
 * text-only rather than a column of broken images.
 */

/** Icons ship under `/icons/<category>/<original filename>`. */
export const ICON_ROOT = '/icons/';

/**
 * True when a path looks like an installed icon.
 *
 * The bundled data supplies `icon` for nearly everything, so an empty string
 * means "this entry has no artwork", not "icons are missing".
 */
export function hasIcon(path: string | null | undefined): path is string {
  return typeof path === 'string' && path.startsWith(ICON_ROOT);
}
