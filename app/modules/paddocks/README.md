# Paddocks

Owns paddock API routes and paddock web routes registered through the
compatibility `web` blueprint.

Current entry points:
- `api_routes.py`
- `routes.py`

The detail page's grazing history chart shows one bar per allocation/session,
ordered by start date, using the same allocated LSU calculation as the table.
These are current reporting estimates, not frozen historical LSU snapshots;
bar widths do not encode session duration. Chart.js loads on pages with history,
and the chart initializes when its details card opens. The table remains available
when JavaScript or the chart CDN is unavailable.
