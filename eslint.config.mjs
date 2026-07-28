import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
  {
    rules: {
      // Two React Compiler rules, downgraded to warnings rather than silenced.
      //
      // `set-state-in-effect` fires on the fetch-on-mount pattern used by five
      // components (`useEffect(() => { load(); }, [load])`, where `load` sets
      // state after an await). `refs` fires on one deliberate ref assignment
      // during render in page.tsx, which exists to keep a Zustand snapshot out
      // of an effect's dependency array.
      //
      // Both are legitimate criticisms and both want the same fix: proper data
      // loading with suspense/error boundaries. That is a real piece of work,
      // it touches every tab, and there are currently no frontend tests to catch
      // a regression — so it is scheduled (docs/AUDIT.md, Phase 2 / A10) rather
      // than rushed. Warnings keep them visible; errors would only invite
      // blanket disable comments.
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/refs": "warn",

      // A leading underscore is the project's marker for a deliberately unused
      // parameter kept for a documented reason — `capabilitiesFor(role, _userId)`
      // holds the seam for per-user permissions.
      "@typescript-eslint/no-unused-vars": [
        "warn",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
]);

export default eslintConfig;
