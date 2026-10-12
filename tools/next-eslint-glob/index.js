/* eslint-disable @typescript-eslint/no-require-imports -- Next's lint plugin requires this CommonJS entrypoint. */
const path = require('node:path');
const { globSync: tinyGlobSync } = require('tinyglobby');

// This adapter supports only the API consumed by @next/eslint-plugin-next@16.4.0.
// Keep fast-glob's directory selection and absolute/relative path semantics;
// tinyglobby otherwise expands literal directories into their descendants.
exports.globSync = (pattern, options) => {
  if (typeof pattern !== 'string' || options?.onlyDirectories !== true ||
      Object.keys(options).some(key => key !== 'onlyDirectories')) {
    throw new TypeError('Unsupported Next ESLint glob call; review the dependency override');
  }
  return tinyGlobSync(pattern, {
    onlyDirectories: true,
    expandDirectories: false,
    absolute: path.isAbsolute(pattern),
  }).map(dir => dir.endsWith('/') && dir !== path.parse(dir).root ? dir.slice(0, -1) : dir);
};
