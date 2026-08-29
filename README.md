# AgriTrack

Flask app for livestock, grazing, and rainfall management across multiple farms.

## Implemented MVP

- Multi-farm CRUD (API + UI)
- Paddock and mob CRUD (API + UI)
- Ledger-backed stock adjustments by animal group type
- Persistent animal cohorts with female reproductive, lactation, and offspring-at-foot state
- Multi-farm breeding-cycle observations and reproductive outcome analytics
- Grazing movement workflow with allocation fractions
- Paddock current weighted stock and grazing history
- Rainfall logging per farm
- Dashboard summary page
- Optional `/api/v1` endpoints plus existing `/api/*` endpoints

## Run locally

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements/dev.txt
set FLASK_APP=manage.py
flask run
```

On Windows, the checked-in PowerShell helpers are the preferred path:

```powershell
.\scripts\setup-venv.ps1
.\scripts\dev.ps1
```

Initialize or update the real farm database with migrations only:

```powershell
$env:FLASK_APP = "manage.py"
.\.venv\Scripts\python -m flask db upgrade
```

Create a private farm user for web and Android login:

```powershell
.\.venv\Scripts\python -m flask user-create --email field@example.com --name "Field User" --password "<password>"
.\.venv\Scripts\python -m flask user-assign-farm field@example.com "Demo Farm" --role manager
```

Seed demo data only for throwaway development databases:

```bash
flask seed-demo
# or
python scripts/seed_demo_data.py
```

Run tests:

```bash
pytest
```

## Private Farm-LAN Mode

For daily private farm use, keep `DATABASE_URL=sqlite:///agritrack.db`, set a
long `SECRET_KEY`, and start the LAN server with:

```powershell
$env:SECRET_KEY = "<long-private-random-value>"
.\scripts\lan.ps1
```

Back up the SQLite database, maps, and task photos:

```powershell
.\scripts\backup.ps1
.\scripts\restore-check.ps1
```

See `docs/private_farm_ops.md` for the farm-LAN runbook.

## Architecture

AgriTrack is organized as a Flask/Jinja modular monolith. Shared app wiring lives
in `app/core`, and feature-owned route modules live under `app/modules`.

See `docs/architecture.md` for the module contract and migration rules.
