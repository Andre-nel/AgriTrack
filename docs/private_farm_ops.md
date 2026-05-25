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
- a small `manifest.json`

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
