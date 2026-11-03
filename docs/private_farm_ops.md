# Private Farm Operations Runbook

AgriTrack is configured for private farm use: one trusted computer runs Flask,
the SQLite database lives under `instance/`, and Android devices connect over
the same Wi-Fi or hotspot.

## One-Time Setup

1. Create or repair the local virtual environment:

   ```powershell
   .\scripts\setup-venv.ps1
   ```

   If an old `.venv` points to a broken Python install, rerun with
   `.\scripts\setup-venv.ps1 -Recreate`.

2. Set private environment values:

   ```powershell
   $env:SECRET_KEY = "<long-private-random-value>"
   $env:DATABASE_URL = "sqlite:///agritrack.db"
   $env:BACKUP_DIR = "backups"
   ```

3. Apply migrations:

   ```powershell
   $env:FLASK_APP = "manage.py"
   .\.venv\Scripts\python -m flask db upgrade
   ```

4. Create the farm user and assign farm access:

   ```powershell
   .\.venv\Scripts\python -m flask user-create --email field@example.com --name "Field User" --password "<password>"
   .\.venv\Scripts\python -m flask user-assign-farm field@example.com "Demo Farm" --role manager
   ```

## Daily LAN Startup

Start the private LAN server:

```powershell
$env:SECRET_KEY = "<long-private-random-value>"
.\scripts\lan.ps1
```

Use the farm computer's LAN IP on Android, for example
`http://192.168.1.25:5000`. The web app requires login; the mobile API keeps
using bearer tokens from Android login.

## Backups

Create a backup zip:

```powershell
.\scripts\backup.ps1
```

The backup includes:

- `instance/agritrack.db`
- `instance/maps`
- `instance/task_attachments`
- `instance/note_attachments`
- `instance/livestock_trade_attachments` (saved trade reference images)
- a small `manifest.json`

Temporary livestock import images are excluded. They expire after 24 hours and
are cleaned up as new import images are uploaded; discard removes the referenced
draft images immediately. Back up the database and saved image directory together.

By default, the newest 30 backups are kept. Verify the newest backup can boot:

```powershell
.\scripts\restore-check.ps1
```

Run backup and restore-check before major data imports, before schema
migrations, and after a heavy field-work day.

## Safety Rules

- Use `flask db upgrade` for real farm data; do not use `db.create_all()` on the
  farm database.
- Keep `instance/` and `backups/` private and out of Git.
- Keep the LAN server on trusted farm networks only.
- Move to Postgres only when you need regular remote access, multiple concurrent
  users, or heavier reporting.

## Polygon weir rollout

1. Schedule a short backend maintenance window and stop writes/sync. Keep queued
   Android commands on their devices. Back up with
   `.\.venv\Scripts\python scripts/backup_agritrack.py --no-prune` and retain the
   archive outside the application directory as part of normal farm backups.
2. Restore that archive to an isolated instance. Run `flask db upgrade` against
   its database, checking revision `b8d4f0a2c613`, ID/timestamp preservation,
   percentage mappings, `PRAGMA foreign_key_check` and `PRAGMA integrity_check`.
   Import the proposed KML twice there; verify stable weir IDs/history, no weir
   paddock, no duplicate water asset and expected full-farm import effects.
3. Deploy backend code and run `flask db upgrade` on the backed-up live database.
   Check integrity before resuming writes. If validation fails, keep writes
   stopped and restore the database and associated instance files from the
   archive together with the previous backend code. Do not use downgrade or
   reset Alembic revisions; percentage observations cannot be downgraded safely.
4. Install the corresponding Android build after backend migration. Confirm
   bootstrap advertises percentage weirs, record a fractional level and an
   overflow transition, and sync an older queued categorical command. Older
   commands remain valid; older displays should be upgraded to show percentages.
5. Import the reviewed KML. From the water workspace, configure each polygon's
   wall and upstream centreline. The default 1 m depth remains visibly preliminary
   until measured. Confirm estimates and weir-fed trough readings before ending
   the rollout. No historical basin shape or historical measured volume is inferred.

For installations already at `a7c3e9f1b502`, the follow-up `b8d4f0a2c613` backfills
polygon weirs' existing capacity field from the configured geometry. Make a fresh
backup first. The atomic SQLite backfill holds a write lock, preserves timestamps
and reading history, and leaves point-only weirs and other assets unchanged.

The 2026-10-31 restored-copy rehearsal converted 36 weirs and 156 history rows,
preserving every existing non-level column and all non-weirs. Huiskamp Boonste
Weir retained its ID and eight history rows across two full KML imports. There
were 41 paddock polygons and no new water assets. The first import also added
one genuine paddock, two gates and one fence under existing full-farm import
behavior; inspect these changes before a live KML import. Both database integrity
checks passed. Private backup/rehearsal artifacts are under ignored `backups/`
and `sandbox/`; this record does not mean production has been migrated.
