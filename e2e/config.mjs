// Shared between the launcher (serve.mjs) and the specs, so the password the
// backend bootstraps its Owner from and the one the tests type are one value.
// MIN_PASSWORD_LENGTH is 10; the bootstrap refuses anything shorter, silently
// from the browser's point of view — the form just says "no accounts exist".
export const E2E_PASSWORD = process.env.E2E_PASSWORD || 'e2e-owner-password-1';
export const E2E_ADMIN_USER = 'admin';
export const BACKEND_PORT = process.env.E2E_BACKEND_PORT || '8419';
export const WEB_PORT = process.env.E2E_WEB_PORT || '3019';
export const BASE_URL = `http://127.0.0.1:${WEB_PORT}`;
