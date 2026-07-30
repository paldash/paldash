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
