# Grazing

Target owner for grazing web workflows, grazing presenters, and grazing-facing
route/form handling.

Current entry points:
- `constants.py`
- `forms.py`
- `presenters.py`
- `routes.py`

Current status:
- Grazing management analytics route and chart payload helpers live here.
- Query normalization and chart payload building are separated from route handlers.
- Mob movement/grazing form handling lives in `app.modules.mobs`.

## Farm LSU history

Grazing Management links to `/analytics/grazing-management/lsu-history`. Repeated
`farm_id` filters select farms; omitted farm filters mean all farms. The
`split_mode` is `aggregate` (the default) or `farm` (separate charts with shared
axes). Date filters are inclusive UTC dates; the default is the last 365 days,
ending today. Today's value is as of the report time, not a projection.

Capacity sums each active paddock's positive grazeable area, falling back to
total area when grazeable area is zero, divided by its stocking-rate override or
the farm default. Farm detail uses the same calculation. Capacity history starts
at the migration's observed baseline and changes when an input is saved. Earlier
capacity is unknown; today's capacity is never projected into the past.

Livestock LSU includes all active inventory registered to a farm, including mobs
without paddock allocations. Open gates and cross-farm paddock allocations do not
duplicate or reassign inventory; whole-mob registered-farm changes do. Conversion
uses the application's existing animal-type LSU factors.

Inventory baselines capture head counts and the net signed ledger quantities
already included, grouped by animal type across cohorts. From the baseline on,
the count at a time is `captured count - captured ledger net + ledger net through
that time`. This supports late postings and excludes future-effective stock until
its recorded date. Farm and active-status changes are replayed independently.
Simultaneous changes are applied as one chart observation.

Older livestock history is shown only where the available counts and ownership
records reconcile. An empty farm has known zero stock from its first capacity or
inventory observation; its earlier stock is unavailable unless a reconciled
ownership chain supports that period. Incomplete opening stock, contradictory moves, unsupported
legacy count entries, or deleted source records may leave gaps. Unknown is never
zero. Capacity and stock have separate coverage; a combined total is unknown if
any selected farm's contribution is unknown. Current recorded balances are shown
separately so a historical gap is not mistaken for today's stock count.

History is written explicitly within existing write transactions. Future farm,
paddock, import, mob-creation, movement, or status write paths must call the
corresponding history recorder before commit. No recorder commits itself and no
report request writes history. Update recording-path tests when adding writers.
Direct SQL maintenance bypasses this contract and must preserve or explicitly
re-establish coverage; it must not silently rewrite an observed baseline.

Apply the Alembic migration with `flask db upgrade` before running the updated
application. It observes current capacity and inventory without inventing past
values. Normal database backups include all new history tables.
