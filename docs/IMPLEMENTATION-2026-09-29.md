# Audit implementation — 29 September 2026

This report supersedes the remaining-work list in [the previous implementation note](IMPLEMENTATION-2026-09-28.md). The user authorized all planned fixes and findings. Changes are on `codex/audit-fixes-server-export`; nothing has been deployed or merged into main, and no live game save has been edited.

## Why PR #16 failed

The PR updated several dependencies but still resolved a vulnerable Next.js/transitive dependency set. Its npm audit failed, while the older container workflow could still pass because it did not depend on that audit. A successful image check therefore did not establish that the dependency gate passed.

[PR #16](https://github.com/paldash/paldash/pull/16) now contains commit `7a7b86ab371776c90e777cd00378254d8319092b`: Next/ESLint 16.3.6, compatible dependency updates, patched transitives, disabled unused image optimization, and the verified standalone tracing fix. **Every remote check passed**: npm, pip, backend, web/e2e/Lighthouse, container, PR title and CodeQL. The PR remains open, not merged.

[PR #17](https://github.com/paldash/paldash/pull/17) appeared during this work for `fast-uri` 3.1.8, already present in the tested baseline. It inherited the same vulnerable baseline as #16. Its isolated repair, commit `1b956890ebd0ce5bd5e8c28600093203dfc6a483`, brings in #16 and verifies that the resulting complete repository tree is identical to the tested dependency repair. **Every remote check passed** on this head as well. Neither dependency PR is merged.

The existing main-branch ruleset now requires npm and pip audits, backend tests, web checks and the container check, with the branch required to be up to date. Its existing deletion and force-push protections remain in place.

## Completed implementation

| Area | Changes and evidence |
|---|---|
| API/privacy | Exact live-REST allowlist; guest/feature enforcement; fail-closed player visibility; explicit public fields; bounded bodies and deadlines; language validation before cache paths. |
| Browser safety | Map text is rendered safely; real Leaflet injection test; compatible framing/content-type/referrer headers; production tracing excludes private directories without deleting Next internals. |
| Save integrity | Ambiguous probes refuse writes; missing pinned world refuses selection; a shared thread/process maintenance lease serializes edits/restores/lifecycle; source identity checks, symlink-resistant atomic replacement and fsync; backups are verified before mutation. |
| World recovery | Durable per-file undo journal; startup/restart/edit inhibition while pending; protected rollback archive; exact-byte recovery and read-back; actual process-exit crash drill; external changes refuse recovery. |
| Legacy exports | Stable multi-file private snapshots; content-bound previews; all retained player/DPS files checked; stronger pruning/reference closure; valid co-op host UID no longer mistaken for nil; verified tar publication; shared concurrency, capacity and cleanup. |
| Dedicated-server exports | Separate selected-player removal preserving retained identities; shared-guild leadership and asset handling; preview/account binding; serialized reference and item-conservation checks; verified download. See [SERVER-EXPORT.md](SERVER-EXPORT.md). |
| Self-export ownership | Immutable account IDs, current linkage and disabled/expiry checks, stale export revocation, strict solo scope, no unpruned fallback, archive containment/hash checks. |
| Persistent work | SQLite export queue; owner-only status/cancel/download; permission/linkage recheck at execution; interrupted running jobs fail instead of replaying; graceful shutdown drains work before releasing the database lease. |
| Dashboard recovery | SQLite online/WAL-consistent backup plus policy; per-file hashes and integrity checks; offline restore/service lease; session/export/job invalidation; rollback journal and actual crash drill. See [RECOVERY.md](RECOVERY.md). |
| Accounts/settings | Required password rotation, accurate throttling, opt-in trusted proxy headers; unsafe settings/types/nonfinite values rejected; external-writer-aware rollback refusal; CSV formula neutralization. |
| Documented bounds | Pocketpair’s stated maxima for base count and workers; 5,000–15,000 pawn sync distance; 0.1–1.0 fishing difficulty. Validate before formatting can round a rejected value into range. No invented limits on other rates. |
| Releases | Minimal workflow permissions, pinned actions/native source, Python 3.11 build/runtime matching and dependency constraints; security audits gate the final image; containerd preserves SBOM/provenance; final OS/library scan precedes publishing the same image bytes. |
| Mined data | Stricter property/row validation recovered 97 invader entries and four fishing groups; corrected affected bundles; full-row digests, canonical sources, localized variants and opacity tracking; 472 decoded tables / 183,228 rows / three explicit refusals. |
| New guides | Twelve source-derived sections: fishing, ponds, baits, expeditions, Operating Table, Souls, crops, cages, recruitment, appeals, stacking flags and base tasks. Exact catalog/reward/object-reference joins; all source values remain offline. |
| Map and loot | Game-provided World Tree bounds and texture references, checked against all 174 travel points; source framing stays explicitly uncalibrated at pixel level. Slot-roll probability and conditional item share are displayed separately. |
| Saved state | Fishing/arena counters, quest IDs/blocks, crop/energy state and staff-only supply events; absent fields stay absent; no fabricated expedition progress, location assignments or event countdowns. Fixed placed-object names/capabilities to read the parser’s `kind` field. |

## Verification

- Final npm and constrained Python advisory checks: **zero known vulnerabilities**.
- Frontend: **186 unit tests**, lint and production build passed; **10 browser tests** passed, including all twelve actual bundled guides and queued export polling.
- Focused queue/data tests: **26 passed**. Final settings/recovery/export-boundary tests: **69 passed**. These overlap the full suite and are not added to its total.
- Multi-file write regression checks: **91 passed**, including real-world restore and player-edit round trips. A full-suite run exposed that the per-file safety recheck mistook the transaction's own first replacement for a game autosave. Verified replacements are now tracked by exact file identity within the maintenance lease; external writes and live-server signals still refuse the next write.
- Full-row catalog recheck: **no change** in digests, variants, opacity or schemas after regeneration.
- Full backend suite, including real-save slow cases: running; final result will replace this line.
- Final implementation-head CI/container scan: pending the review branch.

Evidence is local under `/tmp/paldash-implementation-20260928/`. The two failed fishing-bound tests found that validating the formatted value permitted out-of-range inputs to round into range; validation now checks the requested number. The guide browser test originally counted Next’s empty global route announcer as an error; scoping it to the guide panel fixed the assertion without hiding application errors.

## Acceptance limits

Structural tests do not replace a real game-server load, retained-player reconnect, shared-storage check and save/restart cycle. There is no attached game client, so reconnect checks remain a release acceptance step. A local rootless Podman build is being used for container validation despite the unavailable Docker socket. No production deployment or live game-world mutation was attempted.

The external game process and its restart supervisor must cooperate with maintenance; an advisory file lock cannot control them. Custom native parsers are pinned and exercised, but a clean vulnerability scan is not proof of memory safety. Unknown save schemas, opaque removed references and ambiguous ownership are refused.

Exact quest-title localization, fishing/cage placement joins, saved expedition progress absent from the inspected saves, and native base mutation rates are not verified. The UI exposes the available reference or recorded state and says what is unknown. World Tree framing is source-derived, while independent pixel calibration remains unfinished.

CodeQL’s 49 baseline alert records were reviewed by source flow. Workflow permissions and language-path ordering are repaired; public exception details are reduced. Backup/UID path alerts pass validated identifiers and fixed roots; three cleartext-logging alerts refer to a numeric password-length constant, an audit action label and public game metadata. No mass dismissal or removal of useful diagnostics was performed. Fresh branch analysis remains part of CI review.

Official settings bounds: [Pocketpair configuration parameters](https://docs.palworldgame.com/settings-and-operation/configuration/), reviewed 29 September 2026. Container workflow references: [Docker attestations](https://docs.docker.com/build/metadata/attestations/) and [Trivy image scanning](https://trivy.dev/docs/dev/references/configuration/cli/trivy_image/).
