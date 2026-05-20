# Analytics

Owns analytics web routes, analytics presenters, and chart payload builders.

Current entry points:
- `constants.py`
- `forms.py`
- `presenters.py`
- `routes.py`
- `services.py`

Current status:
- Analytics landing, journal, and stock tracking routes live here.
- Stock tracking chart payloads and journal aggregation are in presenters.
- Manual journal entry creation and transaction handling are in services.
- Grazing management is owned by `app.modules.grazing`.
