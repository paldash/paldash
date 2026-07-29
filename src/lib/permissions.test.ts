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
      ['edit', 'POST', CAPABILITIES.SAVE_EDIT_FULL],
      ['users', 'GET', CAPABILITIES.USERS_MANAGE],
      ['users/alice', 'PATCH', CAPABILITIES.USERS_MANAGE],
      ['audit', 'GET', CAPABILITIES.AUDIT_VIEW],
      ['backup', 'POST', CAPABILITIES.BACKUP_MANAGE],
      ['restore/abc123', 'POST', CAPABILITIES.BACKUP_MANAGE],
      ['settings/ini', 'POST', CAPABILITIES.SETTINGS_WRITE],
      ['server/restart', 'POST', CAPABILITIES.SERVER_CONTROL],
      ['server/stop-container', 'POST', CAPABILITIES.SERVER_CONTROL],
    ])('%s %s needs %s', (path, method, capability) => {
      const verdict = describeSavePath(path, method);
      expect(verdict.allowed).toBe(true);
      expect(verdict.capability).toBe(capability);
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
});
