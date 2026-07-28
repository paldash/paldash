/**
 * Role type, split out from auth.ts so that client components and the
 * permission model can import it without pulling in node:crypto.
 */
export type Role = 'admin' | 'guest';
