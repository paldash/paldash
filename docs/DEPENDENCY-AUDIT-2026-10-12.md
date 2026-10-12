# Dependency audit repair — 12 October 2026

The [scheduled audit](https://github.com/paldash/paldash/actions/runs/38270921788)
failed on main after new JavaScript advisories were published. The Python audit
passed. npm reported eight high-severity package entries; several were parent
packages affected by the same `braces` advisory, rather than independent defects.

The existing npm update in PR #24 supplies Next.js and its ESLint config at
16.4.0. This repair also refreshes the lockfile and enforces patched minimums for
`sharp` (0.35.5) and `source-map-js` (1.2.2).

`braces` 3.0.3 has no patched upstream release. Its only installed dependency
chain was `eslint-config-next` → `@next/eslint-plugin-next` → `fast-glob` →
`micromatch` → `braces`. Next 16.4.0 uses `fast-glob` only for
`globSync(pattern, { onlyDirectories: true })` in `getRootDirs`. The override
scoped to **`@next/eslint-plugin-next@16.4.0`** resolves that dependency to the
small local adapter in `tools/next-eslint-glob`, backed by `tinyglobby@0.2.17`.
It uses `picomatch` instead of `braces`. This tinyglobby package/version was
already used by the TypeScript resolver and Vitest.
The vulnerable dependency chain is removed; no advisory is ignored and no Next
lint rule is disabled. A future Next plugin version requires a fresh compatibility
review because this override deliberately applies to one version only.

Regression tests invoke Next's actual root-discovery helper against temporary
directories: defaults, wildcards, brace patterns, root arrays, separator
normalization and missing paths. The normal lint, frontend, build, browser and
backend checks remain required, as do the unchanged npm/Python/container gates.

The compatibility tests caught two differences in a direct library alias:
tinyglobby returns relative paths with trailing separators and expands literal
directories into their descendants. The adapter explicitly preserves fast-glob's
path semantics and disables that expansion. It refuses calls outside the API
used by this pinned Next plugin. Tests include nested directories and invoke the
actual Next lint rule, requiring it to report native links to a Pages route and
an App Router home page. The Docker dependency-install layer copies the adapter
before running `npm ci`, so clean builds resolve the same local package.

The fresh container build also exposed a Python wheelhouse conflict: building
the native parser without the application constraints downloaded orjson 3.13.0,
while a later requirements pass added pinned orjson 3.12.0. Installing every
wheel then tried to install both versions. The Dockerfile now resolves the
native parser and application requirements in one constrained `pip wheel`
invocation. Runtime Python pins are unchanged; future shared-dependency conflicts
fail during resolution instead of leaving an inconsistent wheelhouse.

The container scan then found patched Debian updates for OpenSSL and PCRE2.
The runtime stage applies stable OS updates, and CI pulls current bases and
rebuilds that stage every time. The eight previously reviewed mitigations were
rechecked against Debian's current advisories and a fresh runtime proof, with
all 44 exact advisory/PURL pairs unchanged. After explicit owner approval, the
same exceptions were renewed to 27 October 2026 at 00:00 UTC; no new
vulnerability was added. Base and deployment images are pinned by digest, with
reviewed updates and stable OS security updates retained. See
[the container assessment](CONTAINER-SECURITY.md).

Sources:

- [Next.js cache-poisoning advisory](https://github.com/advisories/GHSA-mcj8-r9mp-w47p)
- [Sharp/librsvg advisory](https://github.com/advisories/GHSA-wq5f-xc86-pv6w)
- [source-map-js advisory](https://github.com/advisories/GHSA-68fv-2mgg-jv7q)
- [Unpatched braces advisory](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm)
- [Next 16.4.0 root-discovery source](https://github.com/vercel/next.js/blob/v16.4.0/packages/eslint-plugin-next/src/utils/get-root-dirs.ts)
- [tinyglobby source and API](https://github.com/SuperchupuDev/tinyglobby)
