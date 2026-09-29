# Audit follow-through — 28 September 2026

**Current follow-through:** [29 September implementation and verification](IMPLEMENTATION-2026-09-29.md). The earlier plan below is historical.

Local branch: `codex/audit-fixes-server-export`. The initial audit and mined-data review are complete. This branch contains local repairs and a pruned dedicated-server export; it is not deployed or pushed. The remaining work below is part of the fix plan, not a claim that every audit finding is closed.

Read [the original audit](AUDIT-2026-09-28.md) for reproductions and PR evidence, [the mined-data review](MINED-DATA-REVIEW-2026-09-28.md) for verified discoveries, and [the server-export guide](SERVER-EXPORT.md) for the new feature's contract.

## Implemented locally

| Area | Result |
|---|---|
| Dependency PR / A01 | Incorporated PR #16's compatible package updates; patched Next/ESLint to 16.3.6 and vulnerable transitive packages; disabled unused image optimization; switched the production build to Webpack. PR #16 itself remains untouched remotely. A final read-only recheck confirmed the same head (`650203146e60a047bbe4d54f22e52f40a724e796`), still the only open PR, with a failed npm audit and `UNSTABLE` status. |
| Live API / A02–A03 | Exact endpoint allowlist, guest-disable enforcement, privacy lookup for guests and users, refusal when visibility cannot be established, explicit public fields and bounded requests. Encoded traversal and unknown endpoints are refused before the privileged REST call. |
| Map rendering / A04 | Dynamic tooltips use DOM text content. Added framing, content-type and referrer protections, plus a limited compatible CSP. This is not a nonce-based script policy. |
| Save safety / A05–A07 | DNS failures/timeouts remain unknown; missing pinned worlds are refused; maintenance operations share a reentrant thread/process lease; guarded writes recheck before atomic replacement. Backup publication verifies the completed archive against its hashes. |
| Restore / A08 | Preflight all selected targets, replace files atomically, verify read-back and roll back attempted replacements on ordinary failure. A durable crash journal is still outstanding. |
| Self-export / A09 | Bind archives to immutable account IDs, refuse legacy username-only ownership, invalidate relinked accounts, enforce feature/expiry checks at download, recheck scope on the serialized tree and refuse unpruned fallback/unknown player files. The older pipeline still needs coherent snapshots and stronger reference closure. |
| Lifecycle, retention, caches / A10–A12 | Fixed repeated-shutdown deadlock; protected recent rollback backups from the total-count cap; added inode/device/ctime and nanosecond timestamps to file identity checks. |
| Account security / A13 | Enforced temporary-password rotation across API routes, removed capabilities until rotation, made throttle retry time truthful, stopped verification-only password checks creating sessions, and made forwarded-IP trust opt-in. |
| Settings and reports / A14 | Reject unsafe types, control characters, quotes and nonfinite values; verify settings after writing and restore the previous file on ordinary failure; neutralize textual spreadsheet formulas. Full game-specific numeric range validation remains planned. |
| Build and release / A15–A17 | Security audit gates image publication; minimal workflow permissions; immutable action and decoder commits; Node 22; Python 3.11 CI/runtime pairing; constrained Python dependencies; excluded live saves/config/private data from build contexts. |
| Resource bounds / A18–A19 | Limit request bodies before parsing, set network deadlines, reduce overlapping polling and check backend readiness. New server exports have a shared concurrency limit, preview expiry, archive retention and storage quota. Older moderator exports and dashboard-state recovery need more work. |
| Mined-data accuracy | Corrected the client-only-table comparison to include refused server decodes and fixed source-document counts/labels. Newly discovered map, loot, fishing and other features remain planned. |

## Pruned dedicated-server export

A separate panel under **Save tools → World export** removes selected players from a copy while keeping retained player IDs unchanged. It captures a private snapshot, binds the preview to an account and source fingerprint, shows replacement guild leadership, preserves supported shared assets, removes private assets/guilds left empty by the selection, and refuses unresolved references. It rereads serialized files, verifies retained item quantities, and leaves the source unchanged. Output excludes server settings/passwords and dashboard data.

Synthetic tests cover shared workers, leadership, ambiguous containers, missing members, opaque references, tampered plans and private paths. A real-world round trip checks archive scope and an unchanged source. In-game loading and player reconnect/base/storage checks are still required before production use. This account cannot access the Docker daemon, so those checks were not performed.

## Verification

- Frontend: **186 unit tests passed**; lint passed; production build passed; **9 headless browser tests passed** on the generated standalone server.
- Dependencies: final npm and constrained Python requirement scans report **zero known vulnerabilities**. The project virtual environment matches the constrained versions.
- Backend: **2,137 tests passed** in the full suite, including integration and slow cases (44m52s). The **13-test exporter rerun also passed**, including a real-save round trip after the final unrelated-empty-guild correction (10m06s). The latter is a focused final-change recheck, not 13 additional unique tests; the final tree contains 2,138 tests. The earlier complete unit run passed 1,993 tests.
- Real save evidence already obtained: initial pruned-server round trip passed; source fingerprints unchanged; output scope and retained identities/items checked. The final repeat also passed (13 exporter tests in 606 seconds).
- Repository: `git diff --check` passed. No private save/reference/config/database or environment-file paths were found in the standalone output.

Detailed logs are local under `/tmp/paldash-implementation-20260928/`; the original audit's evidence is under `/tmp/paldash-audit-20260928/`. These temporary directories are not publication artifacts.

The first patched Webpack build with the full reference tree present used 894,352 KB maximum RSS (about 873 MiB); this was a build measurement, not proof the standalone package booted. Browser checks subsequently found a real packaging defect: Next's tracer uses `contains: true`, so our old `cache/**` exclusion also removed `use-cache/` and `response-cache/` framework files. Directory-boundary patterns fixed the cause; temporary exact-file includes were removed. Regression coverage uses Next's actual matcher and boots the generated standalone server. The output-path inspection found no save files, server INI, refs or reference-world paths in the standalone artifact.

Local Python tests run in the project's Python 3.14 virtual environment. The constrained packages declare support for Python 3.11, and all 261 Python source files passed a 3.11 syntax check. Actual Python 3.11/container execution remains a CI acceptance step. Neither a clean package scan nor syntax compatibility is a native-library or container-image audit.

## Remaining plan, in order

1. **Before a production release:** add durable restore journaling, recovery/start inhibition and a crash-recovery drill. A multi-file restore is not one atomic transaction. Keep the verified rollback archive available when recovery cannot finish.
2. **Finish self-export privacy acceptance:** migrate the older solo pipeline to coherent multi-file snapshots and the new reference checks; cover live changes, orphan records and unfamiliar schemas. Account ownership repairs do not prove full world-data closure.
3. **Run isolated server acceptance for pruned exports:** retained player login, guild leadership, bases, private/shared storage, removal scope, save/restart cycle. Structural deletion does not anonymize all player-written names.
4. **Finish operational recovery and limits:** SQLite online backup with policy/configuration packaging, session invalidation on recovery, retention and a recovery drill; quotas/cleanup for older moderator solo archives; a persistent job queue for expensive operations.
5. **Complete deployment checks:** rebuild and scan the final container/native libraries, run exact-head CI, make security checks required in branch rules, and review remaining CodeQL findings using the audit's triage. No alerts or rulesets were changed remotely. Configure one shared maintenance-lock path for multiple dashboard containers and ensure the REST endpoint matches the mounted world. External supervisors must not restart the game during maintenance.
6. **Finish settings validation and fault coverage:** derive numeric bounds from a verified schema and expand tests around external writers, symlinks, fsync failures and recovery. A lock only coordinates participants that use it.
7. **Add mined-data features in separate changes:** source-derived World Tree framing and loot-slot probabilities first; then fishing, expeditions, Operating Table, Soul costs and relevant saved progress; finally crop/recruitment/stacking explanations. Preserve the uncertainty and join requirements in the mined-data report. Add catalog provenance/full-row digests and canonical nonlocalized source selection.

Split the work into dependency/build, security/safety and server-export reviews. After the fixed baseline is accepted, regenerate or replace Dependabot PR #16 against it; do not merge the stale vulnerable lockfile. No PR was merged, posted to, closed or pushed here. No live save was mutated.

Additional legacy-scope finding: `exportscope._guid` treats the valid co-op host UID `00000000-0000-0000-0000-000000000001` as empty because it checks a zero prefix rather than the complete nil GUID. The new server exporter uses an exact-nil check and tests such IDs. Correct the legacy helper as part of the solo-export acceptance work, with co-op-to-server source-identity tests; do not treat a zero-prefix GUID as absent.
