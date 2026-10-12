import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { Linter } from 'eslint';
import type { ESLint } from 'eslint';

// Exercise Next's actual consumer of the version-scoped fast-glob ->
// tinyglobby override. A clean audit must not silently disable lint discovery.
const require = createRequire(import.meta.url);
const { getRootDirs } = require('@next/eslint-plugin-next/dist/utils/get-root-dirs') as {
  getRootDirs(context: { cwd: string; settings: { next?: { rootDir?: unknown } } }): string[];
};
const nextPlugin = require('@next/eslint-plugin-next') as ESLint.Plugin;

describe('Next ESLint root discovery with the security override', () => {
  let root: string;
  const discover = (rootDir: unknown) => getRootDirs({ cwd: root, settings: { next: { rootDir } } })
    .sort();

  beforeEach(() => {
    root = mkdtempSync(path.join(tmpdir(), 'paldash-lint-roots-'));
    for (const app of ['one', 'two']) mkdirSync(path.join(root, 'apps', app), { recursive: true });
    mkdirSync(path.join(root, 'apps', 'one', 'nested'));
    writeFileSync(path.join(root, 'apps', 'not-a-directory'), '');
  });

  afterEach(() => rmSync(root, { recursive: true, force: true }));

  it('keeps the working directory when no override is configured', () => {
    expect(getRootDirs({ cwd: root, settings: {} })).toEqual([root]);
  });

  it('finds only directories for a wildcard', () => {
    expect(discover(`${root}/apps/*`)).toEqual([`${root}/apps/one`, `${root}/apps/two`]);
  });

  it('supports brace patterns', () => {
    expect(discover(`${root}/apps/{one,two}`)).toEqual([`${root}/apps/one`, `${root}/apps/two`]);
  });

  it('preserves relative paths without expanding matched directories', () => {
    const relativeRoot = path.relative(process.cwd(), root);
    expect(discover(`${relativeRoot}/apps/*`)).toEqual([
      `${relativeRoot}/apps/one`, `${relativeRoot}/apps/two`,
    ]);
  });

  it('supports an array of literal roots and ignores non-string entries', () => {
    expect(discover([`${root}/apps/two`, null, `${root}/apps/one`])).toEqual([
      `${root}/apps/one`, `${root}/apps/two`,
    ]);
  });

  it('normalizes backslash separators through Next', () => {
    expect(discover(`${root}/apps/one`.replaceAll('/', '\\'))).toEqual([`${root}/apps/one`]);
  });

  it('returns no roots for a missing directory', () => {
    expect(discover(`${root}/missing/*`)).toEqual([]);
  });

  it('still reports native links to discovered Pages and App Router routes', () => {
    mkdirSync(`${root}/apps/one/pages`, { recursive: true });
    writeFileSync(`${root}/apps/one/pages/about.js`, '');
    mkdirSync(`${root}/apps/two/app`, { recursive: true });
    writeFileSync(`${root}/apps/two/app/page.js`, '');
    const messages = new Linter({ cwd: root }).verify(
      'const links = <><a href="/about/">About</a><a href="/">Home</a></>;',
      [{
        languageOptions: { parserOptions: { ecmaFeatures: { jsx: true } } },
        plugins: { next: nextPlugin },
        settings: { next: { rootDir: `${root}/apps/{one,two}` } },
        rules: { 'next/no-html-link-for-pages': 'error' },
      }],
    );
    expect(messages.map(message => message.ruleId)).toEqual([
      'next/no-html-link-for-pages', 'next/no-html-link-for-pages',
    ]);
  });
});
