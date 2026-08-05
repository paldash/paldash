import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';

/**
 * `output: "standalone"` copies traced files out of the project root, and the
 * tracer over-includes: with no excludes it copied `refs/` (5.1 GB — the
 * dedicated-server install, whose PalWorldSettings.ini holds live server
 * passwords) and `refworld/` (a real save with real Steam IDs and player
 * names) into `.next/standalone/`. Measured: 5.8 GB of build output, versus
 * 77 MB once excluded.
 *
 * `.gitignore` and `.dockerignore` both already exclude those directories, so
 * nothing ever shipped — but neither of them governs `.next/`, and the
 * Dockerfile copies `.next/standalone` wholesale out of the builder stage. The
 * three ignore mechanisms have to agree, and only this one is easy to forget.
 *
 * Asserting on the config text rather than importing it: next.config.ts is
 * TypeScript with a `NextConfig` type import, and the point is to catch someone
 * deleting the excludes, which reads the same either way.
 */
describe('next.config.ts output tracing', () => {
  const config = readFileSync(
    path.resolve(__dirname, '../../next.config.ts'),
    'utf8',
  );

  it('declares outputFileTracingExcludes', () => {
    expect(config).toContain('outputFileTracingExcludes');
  });

  // The two that carry real data off this machine. `refs/palworld/` holds live
  // server passwords; `refworld/` is a real world save.
  it.each(['refs/**', 'refworld/**'])('excludes %s from the build output', (pattern) => {
    expect(config).toContain(pattern);
  });

  it('excludes save files and server config wherever they appear', () => {
    expect(config).toContain('**/*.sav');
    expect(config).toContain('**/PalWorldSettings.ini');
  });

  // Turbopack's glob parser rejects character classes with
  // `TurbopackInternalError: Parsing glob pattern`, which fails the build
  // outright rather than degrading. `.gitignore`'s date-prefix pattern for the
  // session transcripts therefore cannot be copied across verbatim.
  it('uses no glob character classes, which Turbopack cannot parse', () => {
    const excludes = config.slice(config.indexOf('outputFileTracingExcludes'));
    expect(excludes).not.toMatch(/\[[^\]]*\]\s*\)?\s*"/);
  });
});

describe('build id and cache headers', () => {
  const config = readFileSync(
    path.join(process.cwd(), 'next.config.ts'),
    'utf8',
  );

  it('emits one build id per build and shares it with the client', () => {
    // The server route and the client component must compare the same string.
    // Two independently-derived ids would never match and the banner would show
    // permanently — worse than not having it.
    expect(config).toContain('generateBuildId');
    expect(config).toContain('NEXT_PUBLIC_BUILD_ID');
    expect(config).toMatch(/BUILD_ID\s*=\s*process\.env\.BUILD_ID/);
  });

  it('never lets the version probe be cached', () => {
    // A cached probe reports the running build as current forever, which is the
    // exact failure the mechanism exists to prevent.
    expect(config).toContain('/api/version');
    expect(config).toMatch(/no-store/);
  });

  it('sets revalidating cache headers for the unhashed public assets', () => {
    // `/_next/static/` is content-hashed and safe to cache forever. `public/`
    // is not: icons and map textures keep their names across regenerations, so
    // they need a revalidation rather than `immutable`.
    expect(config).toMatch(/icons\|maps/);
    expect(config).toContain('must-revalidate');
    // Asserting on the header VALUE, not on the file text: the config mentions
    // `immutable` in the comment explaining why it is not used, and a bare
    // `not.toMatch` fails on the explanation rather than on the behaviour.
    const values = [...config.matchAll(/value:\s*"([^"]*max-age[^"]*)"/g)]
      .map((m) => m[1]);
    expect(values.length).toBeGreaterThan(0);
    expect(values.every((v) => !v.includes('immutable'))).toBe(true);
  });
});
