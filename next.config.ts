import type { NextConfig } from "next";

// One id per build, so a running client can tell it is out of date.
//
// Next already content-hashes everything under `/_next/static/`, so a NAVIGATION
// picks up new code by itself. Nobody navigates a dashboard they leave open —
// which is most of the audience — so a deploy lands invisibly for exactly the
// people using it most. This is what `/api/version` reports and the client
// compares against.
//
// `BUILD_ID` from the environment when CI sets one (a git SHA is ideal, since it
// is meaningful to a human reading a bug report); otherwise the build timestamp,
// which is monotonic and unique per build even if it says less.
const BUILD_ID = process.env.BUILD_ID || `t${Date.now().toString(36)}`;

const nextConfig: NextConfig = {
  output: "standalone",

  // Deterministic per build, and shared with the client through `env` below so
  // both sides compare the same string.
  generateBuildId: async () => BUILD_ID,
  env: { NEXT_PUBLIC_BUILD_ID: BUILD_ID },

  async headers() {
    return [
      {
        // `public/` is NOT content-hashed — icons and map textures keep their
        // names across builds, so a regenerated set can serve stale from a
        // browser or an intervening proxy for as long as it likes.
        //
        // A week rather than a year, and `must-revalidate` rather than
        // `immutable`: these files genuinely do change when a bundle is
        // regenerated, and there is no hash in the URL to break the tie. The
        // trade is a revalidation request against serving a wrong icon
        // indefinitely.
        source: "/:path(icons|maps)/:file*",
        headers: [
          {
            key: "Cache-Control",
            value: "public, max-age=604800, must-revalidate",
          },
        ],
      },
      {
        // The version probe must never be cached — a cached answer says the
        // running build is current forever, which is the exact failure this
        // whole mechanism exists to prevent.
        source: "/api/version",
        headers: [{ key: "Cache-Control", value: "no-store" }],
      },
    ];
  },
  reactStrictMode: true,
  turbopack: {},

  // Standalone output copies traced files out of the project root, and the
  // tracer errs towards including too much: without these excludes it copied
  // `refs/` (5.1 GB — the dedicated-server install, whose PalWorldSettings.ini
  // holds live server passwords) and `refworld/` (a real save with real Steam
  // IDs) into `.next/standalone/`. Measured: 5.8 GB of build output, versus
  // 73 MB once excluded.
  //
  // `.dockerignore` and `.gitignore` already keep both out of the image and out
  // of git, so nothing shipped — but a build artifact that contains them is one
  // misconfigured ignore file away from publishing a real world save, and
  // `docker build` reads `.next/` from the builder stage, not from git.
  //
  // Keep this list in step with the "never leaves this machine" entries in
  // `.gitignore`.
  outputFileTracingExcludes: {
    "*": [
      "refs/**",
      "refworld/**",
      "palworld/**",
      "backups/**",
      "cache/**",
      ".venv/**",
      "**/*.sav",
      "**/PalWorldSettings.ini",
      // Session transcripts dropped in the project root by `/export`; they
      // quote real save contents. `.gitignore` matches them by date prefix,
      // but Turbopack's glob parser rejects character classes outright
      // (`TurbopackInternalError: Parsing glob pattern`), so this is the
      // root-level wildcard instead. Nothing at the root is a `.txt` the
      // runtime needs — `requirements.txt` lives under `backend/`.
      "*.txt",
    ],
  },
};

export default nextConfig;
