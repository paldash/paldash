# Recovery and export operations

## Interrupted world restores

Every multi-file restore first publishes a durable journal and a verified rollback backup. While that journal exists, save mutations and dashboard-controlled server start/restart are refused. Stop the game server, disable any external automatic restart, and use **Backups → Recover interrupted restore**. Recovery restores the bytes that existed immediately before the interrupted operation, then verifies them before clearing the journal. A second interruption is recoverable by repeating the operation.

Recovery refuses a changed target, corrupt undo bytes, a different mounted world or a symlink path. Keep the journal and its protected rollback backup intact if it refuses. Inspect the external change before choosing a manual recovery; deleting the journal would remove the start/write protection.

After a process crash, recently written saves must pass the normal save-activity quiet window before recovery can proceed. Verified writes are recognized only within the active maintenance transaction; that exemption does not survive a restart. REST, TCP and process evidence still apply throughout a multi-file operation, and an external rewrite invalidates the exemption even if it preserves the file's bytes and modification time.

The maintenance lease coordinates dashboard operations. It cannot stop an external supervisor or another program from writing the world. The configured REST endpoint and mounted save directory must refer to the same game instance.

## Dashboard accounts, policy and schedules

Game-save archives do not contain dashboard state. Create a separate snapshot with SQLite's online backup API, which includes committed WAL pages:

```bash
docker compose exec dashboard python3 backend/statebackup.py create
docker compose exec dashboard python3 backend/statebackup.py list
```

Snapshots live in `BACKUP_DIR/dashboard-state`. They contain the dashboard database and access-policy JSON, with a checksum for each member. This includes accounts, password hashes, audit records and schedules, so keep the archives private. Environment secrets and the game configuration are managed separately and are not included. The default retention is ten snapshots (`STATE_BACKUP_KEEP`).

Restore with the dashboard backend stopped and the same cache/backup volumes and environment mounted:

```bash
docker compose stop dashboard
docker compose run --rm --no-deps --entrypoint python3 dashboard backend/statebackup.py restore SNAPSHOT_ID
docker compose up -d dashboard
```

Restore verifies the archive and SQLite integrity, creates its own rollback point, restores policy and database, and invalidates sessions, export records and queued jobs. Sign in again afterward. An interrupted restore blocks backend startup. Recover the previous state before restarting:

```bash
docker compose run --rm --no-deps --entrypoint python3 dashboard backend/statebackup.py recover
```

For a non-container deployment, run `.venv/bin/python backend/statebackup.py` with the same environment and filesystem user as the backend. The service lease prevents a restore while that backend is running. Crash tests exercise actual process exit between replacements for both recovery mechanisms.

## Export queue and capacity

Server, moderator solo and self exports share one cross-process export lock. Preview and creation read a stable private snapshot, and serialized output is read back before publication. Creation is queued in SQLite; leaving the page does not cancel it. The **Export activity** panel shows stages and completed downloads. A queued job can be cancelled. Interrupted running jobs fail explicitly after restart and require a fresh request; they are never replayed against a changed world.

Permissions and account linkage are checked again when queued work starts. Download authorization is checked separately. Running work finishes before the backend releases its state-recovery lease.

`EXPORT_MAX_BYTES` defaults to 2 GiB across the shared export root. Moderator archives expire after `EXPORT_RETENTION_DAYS` (seven by default); self exports keep their existing expiry/cooldown, and server previews expire after one hour, archives after seven days. Storage checks also reserve space for copies. Retention runs on export activity; this is not an unattended disk-cleanup service.

Multiple containers maintaining the same world must share both lock paths on their shared volume:

```dotenv
MAINTENANCE_LOCK_PATH=/app/backups/maintenance.lock
EXPORT_LOCK_PATH=/app/backups/exports.lock
```

Keep the export root outside the source world. An in-game server-load/reconnect check is still required before relying on a pruned copy as a live world; see [SERVER-EXPORT.md](SERVER-EXPORT.md).
