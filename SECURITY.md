# Security policy

## Reporting a vulnerability

Open a **private security advisory** on GitHub (Security → Advisories → Report
a vulnerability) rather than a public issue. If you cannot use advisories,
open an issue saying only "security report, need a private channel" with no
details, and a maintainer will arrange one.

Please include: the route or component, what a caller can do that they should
not be able to, and the role/capability you did it with. A curl transcript
beats prose.

## What counts

This dashboard's security model is documented in `docs/ROLES.md` and the
"Security boundary" section of `AGENTS.md`. In short: the backend
authenticates every request itself (`X-Session-Token` against server-side
sessions), roles and a security-level ceiling gate every write, the Next.js
proxy enforces a route allowlist, and per-player privacy filters are applied
server-side. Reports we especially want:

- Any route reachable without `authz.require` (the `test_route_gates.py`
  sweep should make this impossible — a counterexample is a serious bug).
- Privacy filter bypasses: seeing a player, base or progress detail that a
  privacy mode or `discoveryVisibility` should have hidden.
- Anything that writes to a save file outside `backup.guarded_save_write`,
  or that could corrupt a world.
- Secrets surfacing: `AdminPassword`/`ServerPassword` appearing unmasked in
  any response, log, or audit record.

## What does not count

- Attacks requiring the operator's own shell or compose file.
- The game server's own REST API behaviour (report that to Pocketpair).
- Denial of service by asking for expensive parses — parse throttling is
  best-effort by design and the dashboard is meant for a LAN, not the open
  internet. (`docs/DEPLOYMENT.md` says plainly not to expose it publicly.)

## Supported versions

The latest release and `main`. There are no backports.
