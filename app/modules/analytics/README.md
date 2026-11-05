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

## Water asset history

`/analytics/water-assets` supports a focused asset view through `farm_id` and
`water_asset_id`; asset detail pages link here with suitable fields selected.
An explicit asset selection that does not match the farm/filter scope produces
an empty result, rather than silently widening to other assets. All report
sections and individual charts are native, keyboard-accessible collapsible
sections. Charts initialize only when visible and resize when reopened.

The existing UTC daily state series carries the last known observation forward,
with gaps before the first observation and for explicitly unknown depths.
Weir depth uses a numeric 0–100% axis. Triangles mark Overflowing and circles
mark Under flowing at the measured depth. The tooltip identifies the condition.
An Under flowing reading with unknown depth appears in the summary without a
plotted depth. Other assets
keep their categorical water-level/state charts.

Two optional tracked fields support storage comparisons:

- `capacity_m3`: today's capacity repeated as a dashed reference across the
  selected period, explicitly labelled **Current Capacity Reference**.
- `estimated_volume_m3`: recorded weir depths recalculated with the current
  polygon, centreline and wall depth, using the existing volume service. Each
  distinct depth is calculated once per asset/report. Unknown observations,
  missing/review-required geometry and failed estimates remain gaps or an
  explained unavailable panel; categorical tank/dam levels are not converted
  into invented volumes. Assumed wall depths remain visibly preliminary.

Historical capacity and geometry were not recorded. The current-geometry
projection gives a useful comparison from available depth history without
fabricating old geometry or changing stored observations. Its limitation is
that editing geometry changes the entire estimated-volume series; the capacity
reference does not establish past capacity changes. The chart descriptions,
distinct reference styling, unavailable-state messages and detached model
inputs preserve this distinction. An append-only geometry/capacity history was
deferred because it cannot reconstruct the missing past and would introduce a
new persistence workflow. Reconsider this choice when reports need the capacity
or geometry actually in effect at each date; the repayment approach is dated
configuration snapshots joined to observations, with pre-snapshot periods still
labelled estimates or unavailable. These charts create no new observations or
database migrations.

`tests/integration/test_water_asset_detail.py` checks navigation, scoped edits,
focused filters, analytical volume values, gaps and read-only behavior. Existing
analytics service tests cover daily baselines and categorical states. The weir
browser acceptance suite checks the three focused charts, collapse/reopen,
desktop/mobile layouts and the full Water Network → asset → analytics workflow.
