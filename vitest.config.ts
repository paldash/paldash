import { defineConfig } from 'vitest/config';
import path from 'node:path';

export default defineConfig({
  resolve: {
    alias: { '@': path.resolve(__dirname, 'src') },
  },
  test: {
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
    // `next build` copies src/ into .next/standalone/. Without this, vitest
    // discovers the stale copy as well and reports twice the tests — which would
    // happily stay green against yesterday's build while the real source failed.
    exclude: ['node_modules/**', '.next/**', 'refs/**', 'backend/**'],
  },
});
