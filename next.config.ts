import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
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
