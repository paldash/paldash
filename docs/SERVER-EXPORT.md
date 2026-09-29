# Pruned dedicated-server copies

The World export section in Save tools now has a separate **Export a pruned server world** panel. This feature is implemented on the audit branch with automated structural coverage; an isolated in-game server load is still required before treating it as production-proven.

Use it to give another operator a server world with selected players removed. Retained players keep their original identities. The existing solo/co-op export still remaps a character to a host identity and remains a separate operation.

## Flow

1. Sign in with backup-management permission and select the players to remove. Keep at least one player.
2. Preview the copy. Review the removed player, base, character and container counts. If a removed player leads a surviving guild, review or change the replacement leader.
3. Preview again after changing leadership. Creation accepts only the reviewed plan and refuses if the source world changed or the preview expired.
4. Create and download the archive. The download is bound to the creating account, verified by SHA-256 and retained for seven days.

The source world is read into a private snapshot and is never modified. A changing source is refused, so a quiet save interval is necessary; stopping the source server makes the snapshot stable. A preview lasts one hour. A new preview replaces that account's previous unfinished preview. The shared export root is limited to 2 GiB by default. Creation runs as a persistent background job; progress and verified downloads remain available in Export activity.

## Removal rules

- Remove the selected players' primary and dimensional-storage saves, player characters, private Pals and private inventory containers.
- Preserve retained players' IDs and check their item quantities after pruning and serialization.
- Preserve shared assets of guilds with retained members. Reassign supported shared ownership references and leadership to a retained guild member.
- Remove a guild and its bases/assets when the selection removes all its members. Preserve unrelated guilds that were already empty.
- Clean known ownership history, character handles, work assignments, staged-spawner links and related records.
- Refuse an output with unresolved references to removed IDs, including known GUID encodings in opaque byte payloads. There is no fallback to a full, unpruned world.

Unknown save filenames, symlinked files, inconsistent player identities, orphan player/storage files and ambiguous shared containers need review before export. These refusals protect the copy from silently losing data or retaining an excluded player.

The archive contains only verified save files: `Level.sav`, optional `LevelMeta.sav`, and retained `Players/*.sav` files. It excludes `WorldOption.sav`, server INI files, passwords, dashboard accounts, logs and rotating backups. The receiving server supplies its own settings.

This is structural player removal, not a guarantee of anonymizing player-written text: guild, base and Pal names retained in the world can still contain personal text. Unknown game schemas are refused when they leave a detectable removed reference; parser checks cannot prove every game-side invariant.

## Acceptance before using a copy as a server

Keep the original world and archive unchanged. Load an extracted copy on an isolated server with its own save/configuration directory and ports. Do not extract over a running server or its only world copy. Check that retained players reconnect to their existing characters, shared guild leadership works, bases/workers/storage load correctly, removed characters are absent, and a save/restart cycle succeeds.

Local tests cover shared-guild behavior, refusal cases, archive scope, write/read-back integrity and an unchanged source fixture. The dashboard image and native codec were built and tested with rootless Podman. An actual game-server load and retained-player reconnect have not been performed, and no game client is attached. These remain acceptance steps that structural tests cannot substitute for.

The operator has reported a successful in-game solo-world export. That validates their solo-export experience; dedicated-server pruning and retained-player reconnect remain a separate acceptance check.
