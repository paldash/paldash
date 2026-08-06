import { describe, expect, it } from 'vitest';
import { describeSavePath, CAPABILITIES, FEATURES } from './permissions';

/**
 * The proxy route allowlist.
 *
 * This is a security boundary: the Python backend has no listener of its own on
 * the network, so whatever this function permits is what the outside world can
 * reach. It replaced a prefix-matching scheme whose default branch made every
 * new backend route reachable the moment it existed.
 */
describe('describeSavePath', () => {
  describe('rejects anything not explicitly allowed', () => {
    it.each([
      'unknown',
      'auth/login',
      'auth/logout',
      'auth/session',
      'admin',
      '',
      'edit/sort/everything',
      'users/bob/promote',
    ])('refuses %s', (path) => {
      expect(describeSavePath(path, 'GET').allowed).toBe(false);
    });

    it('refuses auth endpoints so the proxy cannot mint sessions', () => {
      // Login is reached directly by the Next.js auth route, never proxied.
      expect(describeSavePath('auth/login', 'POST').allowed).toBe(false);
    });
  });

  describe('rejects path traversal', () => {
    it.each([
      '../auth/login',
      'bases/../../etc/passwd',
      '..',
      '../../secret',
      '/absolute',
      'double//slash',
    ])('refuses %s', (path) => {
      const verdict = describeSavePath(path, 'GET');
      expect(verdict.allowed).toBe(false);
      expect(verdict.reason).toBeDefined();
    });
  });

  describe('enforces the method', () => {
    it('allows GET on a read route but not POST', () => {
      expect(describeSavePath('bases', 'GET').allowed).toBe(true);
      expect(describeSavePath('bases', 'POST').allowed).toBe(false);
    });

    it('allows POST on a write route but not DELETE', () => {
      expect(describeSavePath('edit/sort/all', 'POST').allowed).toBe(true);
      expect(describeSavePath('edit/sort/all', 'DELETE').allowed).toBe(false);
    });

    it('separates read and write capabilities on the same path', () => {
      expect(describeSavePath('policy', 'GET').capability).toBe(CAPABILITIES.VIEW_BASIC);
      expect(describeSavePath('policy', 'POST').capability).toBe(CAPABILITIES.POLICY_MANAGE);
    });
  });

  describe('maps routes to the right capability', () => {
    it.each([
      ['edit/sort/stackables', 'POST', CAPABILITIES.SAVE_SORT_STACKABLES],
      ['edit/sort/all', 'POST', CAPABILITIES.SAVE_SORT_ALL],
      ['edit/pals/bulk', 'POST', CAPABILITIES.SAVE_EDIT_FULL],
      ['edit/pals/bulk/preview', 'POST', CAPABILITIES.SAVE_EDIT_FULL],
      ['edit/container/abc-123/slots', 'POST', CAPABILITIES.SAVE_EDIT_FULL],
      ['edit/container/abc-123/slots/preview', 'POST', CAPABILITIES.SAVE_EDIT_FULL],
      // Scanning for illegal Pals is how an admin finds out whether anyone has
      // been cheating, so it must not require the write capability.
      ['edit/pal-containers', 'GET', CAPABILITIES.SAVE_EDIT_FULL],
      ['edit/pal/clone', 'POST', CAPABILITIES.SAVE_EDIT_FULL],
      ['edit/pal/clone/preview', 'POST', CAPABILITIES.SAVE_EDIT_FULL],
      ['edit/pal/import', 'POST', CAPABILITIES.SAVE_EDIT_FULL],
      ['edit/pal/import/preview', 'POST', CAPABILITIES.SAVE_EDIT_FULL],
      // A Pal export is a read, at the same gate as every other export.
      ['export/pal', 'GET', CAPABILITIES.VIEW_SELF],
      ['palcheck/scan', 'GET', CAPABILITIES.VIEW_DETAIL],
      ['palcheck/repair', 'POST', CAPABILITIES.SAVE_EDIT_FULL],
      ['palcheck/repair/preview', 'POST', CAPABILITIES.SAVE_EDIT_FULL],
      // Moderation is its own capability: taking the server down and banning a
      // player are different trusts and must be grantable separately.
      ['moderate/kick', 'POST', CAPABILITIES.PLAYERS_MODERATE],
      ['moderate/ban', 'POST', CAPABILITIES.PLAYERS_MODERATE],
      ['moderate/unban', 'POST', CAPABILITIES.PLAYERS_MODERATE],
      ['moderate/announce', 'POST', CAPABILITIES.PLAYERS_MODERATE],
      ['moderate/bans', 'GET', CAPABILITIES.PLAYERS_MODERATE],
      ['server/shutdown', 'POST', CAPABILITIES.SERVER_CONTROL],
      ['server/force-stop', 'POST', CAPABILITIES.SERVER_CONTROL],
      ['server/save', 'POST', CAPABILITIES.SERVER_CONTROL],
      ['metrics/history', 'GET', CAPABILITIES.VIEW_BASIC],
      ['metrics/summary', 'GET', CAPABILITIES.VIEW_BASIC],
      ['users', 'GET', CAPABILITIES.USERS_MANAGE],
      ['users/alice', 'PATCH', CAPABILITIES.USERS_MANAGE],
      ['audit', 'GET', CAPABILITIES.AUDIT_VIEW],
      ['backup', 'POST', CAPABILITIES.BACKUP_MANAGE],
      ['restore/abc123', 'POST', CAPABILITIES.BACKUP_MANAGE],
      ['settings/ini', 'POST', CAPABILITIES.SETTINGS_WRITE],
      ['server/restart', 'POST', CAPABILITIES.SERVER_CONTROL],
      ['server/stop-container', 'POST', CAPABILITIES.SERVER_CONTROL],
      // The item catalogue and one item's sources are the same disclosure —
      // what the GAME has — so they share a gate. `bases/craftable` is derived
      // from container contents and takes the storage gate instead.
      ['world/items', 'GET', CAPABILITIES.VIEW_BASIC],
      ['world/items/AIcore', 'GET', CAPABILITIES.VIEW_BASIC],
      ['bases/craftable', 'GET', CAPABILITIES.VIEW_SELF],
      // The checklist detail must take the SAME gate as the summary it details.
      // A stricter one makes the tab unreachable wherever the counts work,
      // which reads as broken rather than as policy.
      ['progress', 'GET', CAPABILITIES.VIEW_SELF],
      ['progress/detail', 'GET', CAPABILITIES.VIEW_SELF],
    ])('%s %s needs %s', (path, method, capability) => {
      const verdict = describeSavePath(path, method);
      expect(verdict.allowed).toBe(true);
      expect(verdict.capability).toBe(capability);
    });

    it('the item-source pattern does not open a path under the catalogue', () => {
      // `world/items/{id}` sits directly under `world/items`, so a lazy pattern
      // here is how a second segment becomes reachable. Ids are the game's own
      // and contain only letters, digits and underscores.
      expect(describeSavePath('world/items/AIcore/extra', 'GET').allowed).toBe(false);
      expect(describeSavePath('world/items/../players', 'GET').allowed).toBe(false);
      expect(describeSavePath('world/items/a-b', 'GET').allowed).toBe(false);
      // And it is a read. The catalogue is bundled data nothing can write to.
      expect(describeSavePath('world/items/AIcore', 'POST').allowed).toBe(false);
    });

    it('no longer exposes the retired general edit route', () => {
      // `/api/edit` was a 501 placeholder. The specific routes replaced it, and
      // an allowlist entry pointing at nothing is an invitation to re-add a
      // catch-all write endpoint by accident.
      expect(describeSavePath('edit', 'POST').allowed).toBe(false);
    });

    it('a Pal cannot be named "bulk" into the batch route', () => {
      // The batch lives under `pals/`, not `pal/`, so a single-Pal edit can
      // never be mistaken for it — nor the reverse.
      expect(describeSavePath('edit/pal/bulk', 'POST').capability).toBe(
        CAPABILITIES.SAVE_EDIT_FULL
      );
      expect(describeSavePath('edit/pals/anything-else', 'POST').allowed).toBe(false);
    });

    it('never lets a sort route fall through to the weaker capability', () => {
      // `edit/sort/all` must not be satisfied by the stackables capability.
      expect(describeSavePath('edit/sort/all', 'POST').capability).not.toBe(
        CAPABILITIES.SAVE_SORT_STACKABLES
      );
    });
  });

  describe('guest visibility', () => {
    it('marks public reads with the feature that gates them', () => {
      expect(describeSavePath('bases', 'GET').feature).toBe(FEATURES.BASES);
      expect(describeSavePath('mapobjects', 'GET').feature).toBe(FEATURES.MAP_OBJECTS);
      expect(describeSavePath('items', 'GET').feature).toBe(FEATURES.ITEMS);
    });

    it('leaves privileged reads with no guest feature', () => {
      // A null feature means "signed in only" — a guest can never reach it.
      expect(describeSavePath('players', 'GET').feature).toBeNull();
      expect(describeSavePath('progress', 'GET').feature).toBeNull();
      expect(describeSavePath('audit', 'GET').feature).toBeNull();
      expect(describeSavePath('users', 'GET').feature).toBeNull();
    });
  });

  describe('parameterised paths', () => {
    it('accepts well-formed identifiers', () => {
      expect(describeSavePath('restore/a1b2c3', 'POST').allowed).toBe(true);
      expect(describeSavePath('users/alice.smith-1_x', 'DELETE').allowed).toBe(true);
      expect(describeSavePath('inventory/abcd-1234', 'GET').allowed).toBe(true);
    });

    it('refuses identifiers containing separators or junk', () => {
      expect(describeSavePath('restore/a1b2/c3', 'POST').allowed).toBe(false);
      expect(describeSavePath('users/alice bob', 'DELETE').allowed).toBe(false);
      expect(describeSavePath('restore/', 'POST').allowed).toBe(false);
    });
  });

  describe('paldeck', () => {
    it('is readable at VIEW_BASIC, both listing and entry', () => {
      expect(describeSavePath('world/paldeck', 'GET')).toMatchObject({
        allowed: true,
        capability: CAPABILITIES.VIEW_BASIC,
      });
      expect(describeSavePath('world/paldeck/SheepBall', 'GET')).toMatchObject({
        allowed: true,
        capability: CAPABILITIES.VIEW_BASIC,
      });
    });

    it('is read-only', () => {
      expect(describeSavePath('world/paldeck', 'POST').allowed).toBe(false);
      expect(describeSavePath('world/paldeck/SheepBall', 'DELETE').allowed).toBe(false);
    });

    it('refuses a species id containing separators', () => {
      // The id goes into a path segment; anything with a slash or dot must not
      // reach the backend as a species name.
      expect(describeSavePath('world/paldeck/../build', 'GET').allowed).toBe(false);
      expect(describeSavePath('world/paldeck/a.b', 'GET').allowed).toBe(false);
      expect(describeSavePath('world/paldeck/a-b', 'GET').allowed).toBe(false);
    });
  });

  describe('bundled data pack reload', () => {
    it('is a POST gated on POLICY_MANAGE', () => {
      expect(describeSavePath('world/packs/reload', 'POST')).toMatchObject({
        allowed: true,
        capability: CAPABILITIES.POLICY_MANAGE,
      });
    });

    it('is not readable, and not guest-visible', () => {
      // It mutates process state (drops caches) even though it writes no file,
      // so it must not be reachable as an ordinary read.
      expect(describeSavePath('world/packs/reload', 'GET').allowed).toBe(false);
      expect(describeSavePath('world/packs/reload', 'POST').feature).toBeNull();
    });

    it('does not open a path to anything else under world/packs', () => {
      expect(describeSavePath('world/packs', 'POST').allowed).toBe(false);
      expect(describeSavePath('world/packs/regenerate', 'POST').allowed).toBe(false);
    });
  });

  describe('per-base storage and reports (Phase 5)', () => {
    it('lets a Player reach storage, for the backend to scope', () => {
      // Your own base's contents are something you can walk up to in game, and
      // gating this at VIEW_DETAIL left a Player seeing their guild's *total*
      // Wood on the Items tab but not which of their own chests it was in.
      //
      // The allowlist cannot tell whose base an id names, so it only decides
      // whether the request reaches the backend — `_own_guild_base_ids` there
      // narrows the list to the caller's own guilds below VIEW_DETAIL.
      for (const path of ['bases/storage', 'bases/abcd-1234/storage', 'inventory/abcd-1234']) {
        expect(describeSavePath(path, 'GET')).toMatchObject({
          allowed: true,
          capability: CAPABILITIES.VIEW_SELF,
        });
      }
    });

    it('still keeps storage above the anonymous base list', () => {
      // `bases` is a map pin a guest can be shown; storage is an inventory
      // readout that needs a linked account at minimum. The gap narrowed from
      // VIEW_BASIC->VIEW_DETAIL to VIEW_BASIC->VIEW_SELF; it did not close.
      expect(describeSavePath('bases', 'GET').capability).toBe(CAPABILITIES.VIEW_BASIC);
      expect(describeSavePath('bases/storage', 'GET').capability).toBe(CAPABILITIES.VIEW_SELF);
    });

    it('allows report listing and rendering', () => {
      expect(describeSavePath('reports', 'GET').allowed).toBe(true);
      expect(describeSavePath('reports/base-items', 'GET').allowed).toBe(true);
      expect(describeSavePath('reports/world-items', 'GET').allowed).toBe(true);
    });

    it('refuses writes to read-only report and storage routes', () => {
      expect(describeSavePath('reports/base-items', 'POST').allowed).toBe(false);
      expect(describeSavePath('bases/storage', 'DELETE').allowed).toBe(false);
      expect(describeSavePath('bases/abcd/storage', 'POST').allowed).toBe(false);
    });

    it('allows the structured export routes', () => {
      for (const kind of ['world', 'guild', 'base', 'container']) {
        expect(describeSavePath(`export/${kind}`, 'GET')).toMatchObject({
          allowed: true,
          capability: CAPABILITIES.VIEW_DETAIL,
        });
      }
      expect(describeSavePath('export/verify', 'POST').allowed).toBe(true);
    });

    it('lets a plain Player reach their own character and Pal exports', () => {
      // Exporting *your own* character is the same class of act as reading your
      // own palbox, and a Player who cannot get their Pals out has no way to
      // move a character between servers without asking an admin.
      //
      // This allowlist cannot tell whose id is in the query string, so it only
      // decides whether the request reaches the backend at all — `_owns_export_subject`
      // there rejects anyone else's id, and fails closed on an unlinked account.
      for (const kind of ['player', 'pal']) {
        expect(describeSavePath(`export/${kind}`, 'GET')).toMatchObject({
          allowed: true,
          capability: CAPABILITIES.VIEW_SELF,
        });
      }
    });

    it('refuses unknown export kinds and wrong methods', () => {
      expect(describeSavePath('export/everything', 'GET').allowed).toBe(false);
      expect(describeSavePath('export/world', 'POST').allowed).toBe(false);
      // `verify` is a POST endpoint, not a kind that can be exported.
      expect(describeSavePath('export/verify', 'GET').allowed).toBe(false);
    });

    it('gates the import dry run behind the editor capability', () => {
      expect(describeSavePath('import/preview', 'POST')).toMatchObject({
        allowed: true,
        capability: CAPABILITIES.SAVE_EDIT_FULL,
      });
    });

    it('gates the import write behind the editor capability', () => {
      expect(describeSavePath('import/apply', 'POST')).toMatchObject({
        allowed: true,
        capability: CAPABILITIES.SAVE_EDIT_FULL,
      });
    });

    it('exposes no import route other than preview and apply', () => {
      for (const path of ['import', 'import/commit', 'import/container', 'import/world']) {
        expect(describeSavePath(path, 'POST').allowed).toBe(false);
      }
      expect(describeSavePath('import/preview', 'GET').allowed).toBe(false);
      expect(describeSavePath('import/apply', 'GET').allowed).toBe(false);
    });

    it('exposes the edit schema as a read, not a write', () => {
      // The UI renders its editor from the schema; needing the write capability
      // just to see the bounds would hide the editor from people who can read.
      expect(describeSavePath('edit/schema/pal', 'GET')).toMatchObject({
        allowed: true,
        capability: CAPABILITIES.VIEW_DETAIL,
      });
      expect(describeSavePath('edit/schema/guild', 'GET').allowed).toBe(false);
    });

    it('gates Pal editing behind the editor capability', () => {
      expect(describeSavePath('edit/pal/abcd-1234/preview', 'POST')).toMatchObject({
        allowed: true,
        capability: CAPABILITIES.SAVE_EDIT_FULL,
      });
      expect(describeSavePath('edit/pal/abcd-1234', 'POST')).toMatchObject({
        allowed: true,
        capability: CAPABILITIES.SAVE_EDIT_FULL,
      });
    });

    it('gates player editing behind the editor capability', () => {
      expect(describeSavePath('edit/player/abcd-1234/preview', 'POST')).toMatchObject({
        allowed: true,
        capability: CAPABILITIES.SAVE_EDIT_FULL,
      });
      expect(describeSavePath('edit/player/abcd-1234', 'POST')).toMatchObject({
        allowed: true,
        capability: CAPABILITIES.SAVE_EDIT_FULL,
      });
    });

    it('refuses malformed character-edit paths', () => {
      expect(describeSavePath('edit/pal/abcd/../player/x', 'POST').allowed).toBe(false);
      expect(describeSavePath('edit/player/abcd/extra', 'POST').allowed).toBe(false);
      expect(describeSavePath('edit/guild/abcd', 'POST').allowed).toBe(false);
      expect(describeSavePath('edit/player/abcd-1234', 'GET').allowed).toBe(false);
    });

    it('never exposes either import route to guests', () => {
      expect(describeSavePath('import/preview', 'POST').feature).toBeNull();
      expect(describeSavePath('import/apply', 'POST').feature).toBeNull();
    });

    it('never exposes exports to guests', () => {
      // feature: null means a signed-out caller is refused outright, regardless
      // of the guest visibility policy. Exports carry real Steam IDs.
      expect(describeSavePath('export/world', 'GET').feature).toBeNull();
      expect(describeSavePath('export/player', 'GET').feature).toBeNull();
    });

    it('refuses malformed storage and report paths', () => {
      expect(describeSavePath('bases/abcd/storage/extra', 'GET').allowed).toBe(false);
      expect(describeSavePath('bases//storage', 'GET').allowed).toBe(false);
      expect(describeSavePath('reports/../backups', 'GET').allowed).toBe(false);
      expect(describeSavePath('reports/Base_Items', 'GET').allowed).toBe(false);
    });

    it('refuses malformed moderation paths', () => {
      expect(describeSavePath('moderate', 'POST').allowed).toBe(false);
      expect(describeSavePath('moderate/../users', 'POST').allowed).toBe(false);
      expect(describeSavePath('moderate/kick/extra', 'POST').allowed).toBe(false);
      // Reads and writes are not interchangeable here.
      expect(describeSavePath('moderate/kick', 'GET').allowed).toBe(false);
      expect(describeSavePath('moderate/bans', 'POST').allowed).toBe(false);
    });

    it('keeps moderation and server control as separate capabilities', () => {
      // The whole point of the split: granting one must not imply the other.
      expect(describeSavePath('moderate/ban', 'POST').capability).not.toBe(
        describeSavePath('server/shutdown', 'POST').capability
      );
    });

    it('never exposes moderation to guests', () => {
      expect(describeSavePath('moderate/kick', 'POST').feature).toBeNull();
      expect(describeSavePath('moderate/bans', 'GET').feature).toBeNull();
    });
  });
});
