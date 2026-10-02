# Analytics

Owns analytics web routes, analytics presenters, and chart payload builders.

Current entry points:
- `constants.py`
- `forms.py`
- `presenters.py`
- `routes.py`
- `services.py`
- `stock_tracking.py`

Current status:
- Analytics landing, journal, and stock tracking routes live here.
- Stock tracking chart payloads and journal aggregation are in presenters.
- Manual journal entry creation and transaction handling are in services.
- Grazing management is owned by `app.modules.grazing`.

## Stock count history

Stock Tracking and the mob stock history page share the chart, animal-type and
date filters, grouping controls, and chart payload builder. Clearing every group
field shows one total. The analytics page also supports multiple farm filters
and grouping by farm.

Charts show timestamped stock ledger changes in UTC. Entries at the same timestamp
within a plotted group form one atomic change, and hovering or selecting a point
shows its constituent ledger events and original recorded notes. Notes are
displayed as text, including long and multiline notes. Opening and closing points
do not invent recorded events; current-balance reconciliation is an opening offset.

Missing-stock events and notes containing `missing`, `kort`, `short`, `mis`, or
`weg` are marked with red diamonds. Matching ignores case and uses whole words,
without changing the saved event or note. A simultaneous group of changes with
zero net change still retains its highlighted marker and notes. Zoom, pan, reset,
and legend toggles support chart inspection.
