# Fences

Owns fence section API routes and web routes registered through the compatibility
`web` blueprint.

Current entry points:
- `api_routes.py`
- `forms.py`
- `routes.py`

Map editing API:
- `POST /api/fences` creates manual fence sections from GeoJSON line geometry.
- `PATCH /api/fences/<fence_section_id>` updates core fields, paddock links, and geometry.
- `DELETE /api/fences/<fence_section_id>` archives a section while preserving history.
