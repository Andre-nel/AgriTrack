# AgriTrack

Flask app for livestock, grazing, and rainfall management across multiple farms.

## Implemented MVP

- Multi-farm CRUD (API + UI)
- Paddock and mob CRUD (API + UI)
- Ledger-backed stock adjustments by animal group type
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

Initialize DB tables quickly for local dev:

```bash
flask shell -c "from app.extensions import db; db.create_all()"
```

Seed demo data:

```bash
flask seed-demo
# or
python scripts/seed_demo_data.py
```

Run tests:

```bash
pytest
```
