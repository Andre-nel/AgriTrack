# Water

Owns water network API routes, web routes registered through the compatibility
`web` blueprint, and water form parsing.

Current entry points:
- `api_routes.py`
- `forms.py`
- `routes.py`

## Asset detail pages

Asset cards and map features in **Water Network** open the farm-scoped detail
page at `/farms/<farm_id>/water/assets/<asset_id>`. Other map views keep their
popups, whose **Open asset** action links to the same page. The detail page shows
current state and storage information, the type-aware editor, linked tasks,
connections, notes/attachments and the latest 50 state observations. It shares
the editor configuration, field template and validation with the workspace;
there is no separate set of asset editing rules. Asset, note and connection
forms use a fixed `return_to=asset` value to return to the detail page after
success or validation errors. Existing workspace editor URLs remain supported.

**Configure weir geometry** appears for weirs. **Asset analytics** opens the
existing Water Assets report filtered to that asset, including inactive assets.
Weirs initially show recorded depth, a current capacity reference and estimated
volume history. Filters, trend charts, individual figures and statistics can
each be collapsed. See the [analytics module](../analytics/README.md#water-asset-history)
for the historical-estimate basis and its limitations.

## Water levels and trough supply

Weirs store `water_level_percent` (0–100, at most two decimal places) separately
from the flow condition. Overflowing requires 100%. Under flowing accepts any
measured depth, including unknown, and automatically sets the weir's status to
Damaged. Clearing Under flowing allows the operator to change status again.
The percentage measures maximum water depth at the wall, not stored volume.
Other asset types keep categorical levels; ground dams still support Overflowing.
Legacy weir categories and queued mobile commands pass through
`app/services/water_level.py`. API `water_level` remains a derived numeric string
or a flow token (`overflowing` or `underflowing`). `water_level_label` supplies the display text.

Mass Update displays altitude in metres as a read-only value. Latitude and
longitude are available in the individual asset editor. Mass Update ignores
all three location measurements in submitted forms.

A trough with any active upstream connection path containing a weir displays its
own reported reading, even when that source is dry or inactive. Supply warnings
still reflect upstream availability. Mixed weir/non-weir sources follow this
rule. Other troughs retain existing source mirroring.

## Polygon storage estimates

Import a WR polygon as the full-water outline, then choose **Configure storage**
in the water workspace or map popup. In the local, north-up outline editor,
review the suggested wall and centreline, and enter maximum depth from the bed
at the wall to overflow level. A weir with a location point and no saved line
defaults its wall to the nearest point along the exterior outline, including
the middle of an edge. The farthest outline point from that wall is the upstream
endpoint. Those endpoints split the exterior into two opposing banks; the
suggested centreline follows their middle through bends. Saved lines, including
ones awaiting outline review, are preserved. Loading or previewing a suggestion
does not persist it; **Save geometry** confirms it. Without a location point,
choose the wall and trace a line manually. **Start new line** always allows
manual tracing, and suggested handles can be dragged or adjusted with arrow keys.
The prefilled **1 m is a preliminary assumption**; clear that checkbox only when
using a measured depth. Preview percentages do not create observations.

The geometry editor shades estimated water coverage in dark blue over the pale
full-water outline. It loads at the displayed **Preview water depth (%)** and
updates when that percentage or the finished geometry changes. At 0% the dark
fill is empty; at 100% it covers the outline, excluding islands. Intermediate
levels advance from the wall through bends according to the existing linear-bed
model, not a fraction of the bounding box, polygon area or storage volume.
The shading is a model preview, not a surveyed shoreline. Missing or invalid
geometry, invalid inputs and failed calculations clear the shading and show a
reason; an unavailable preview is distinct from a known empty weir.

`POST /api/water-assets/<id>/volume-preview` accepts optional boolean
`include_water_fill`. When true, `water_fill` contains geographic polygons clipped
at the water plane from the cached volume model's positive surface triangles.
The SVG viewer must clip these display pieces to the full polygon using the
even-odd rule to exclude its holes. An empty list means no coverage; null means
unavailable or unknown. Ordinary asset responses omit the display geometry.
Requests are debounced, cancelled on edits, and matched to their input snapshot
so an older response cannot repaint a newer preview. Pan and zoom reuse the
current layer. Neither automatic shading nor the Preview button records levels
or saves geometry. The existing mesh and cache limits bound calculation and
retained surface data; no separate depth model or geometry dependency is added.

The default is an editable approximation, not a surveyed channel or an exact
medial axis. Each bank is sampled at 128 equal length intervals so uneven KML
vertex spacing does not bias the result. Initial midpoints are corrected toward
equal distance from the two banks with at most 12 local iterations and a 1 cm
distance-difference tolerance. Handles are simplified within 0.25 m; if this
would invalidate the line, the unsimplified draft is tried. The same topology
validator as manual drawing rejects bank crossings, self-intersections and
islands. A failed suggestion retains the snapped wall and explains that manual
tracing is needed. Bounded sampling keeps page loads predictable without a new
geometry dependency; very narrow, branching or intricate outlines may require
manual tracing. If such outlines become common, reconsider the sampling or a
constrained medial-axis algorithm using those failures as regression fixtures.

`weir_volume_service.py` uses the existing `polygon_geometry.py` triangulation
and clipping primitives. Coordinates are stored geographically and projected to
local metres for calculation. A single connected polygon may contain holes.
The centreline must remain inside the water outline and outside holes, without
crossing itself. Endpoints within 1 m of the outer boundary are snapped to it.
Disconnected basins need separate models and are rejected.

For centreline length L, nearest projected upstream distance s, wall depth H,
and fraction p, full depth is H(1−s/L), and water depth is H max(0,p−s/L).
Integrating this depth across the polygon accounts for changing wetted area.
Overflowing uses p=1. A 100 × 10 m rectangle with H=1 m holds 500 m³ at full
level and 125 m³ at 50% wall depth. The calculated full-water storage is the asset’s existing `capacity_m3`.
Polygon weirs have one capacity value, managed from their geometry; manual
capacity entry remains available for point-only weirs and other storage assets.
Geometry changes recalculate this value, or clear it when review/setup or
convergence prevents calculation. The preliminary model labels still apply.

The mesh is refined adaptively and checked against a finer mesh at near-empty,
intermediate and full levels, plus the requested current level. Differences must
be within max(0.1 m³, 0.1% of capacity). Refinement is bounded by depth and triangle
count; failure withholds estimates and returns an explanation. Prepared geometry
is cached in process by polygon, centreline, height and model version. Changing
any input invalidates that cache. Numerical convergence does not establish the
physical accuracy of the assumed bed.

The preliminary model assumes **vertical banks and an inferred linear bed**.
Nearest-centreline stationing makes bends usable without surveyed depths, but
can change abruptly across segment bisectors and cannot represent real shelves,
sediment or bank slopes. This is a deliberate modelling limitation: it gives a
repeatable estimate from the available outline and wall depth without inventing
survey data. Surveyed bathymetry and sloping-bank models are deferred because
those measurements are unavailable. Maintain the model version, convergence
checks and visible estimate labels; reconsider this model when surveyed depths
become available, replacing the inferred field with measured bed elevations.
Historical percentages are not retrospectively presented as measured volumes.

Point-only weirs remain usable. Missing setup, changed outlines awaiting review,
and invalid/unconverged geometry produce no estimate. Unknown levels produce no
current volume, while capacity can still be estimated. Unchanged reimports keep
configuration; changed outlines require a saved, validated centreline review.

## Percentage migration and rollout

Revision `a7c3e9f1b502` converts current and both previous/current historical weir
levels: Empty=0, Low=25, Half=50, High=75, Full=100, Overflowing=100 with its flag,
and unknown=null. It clears obsolete categorical storage for weirs. It preserves
IDs, timestamps, non-weirs and the earlier estimated overflowing periods below.
Unexpected values abort before schema changes. Repeated upgrades do not reconvert.
Lossy downgrade is blocked; rollback requires the pre-migration backup.

Revision `b8d4f0a2c613` backfills the single `capacity_m3` value for already imported
polygon weirs using their current geometry. Review/setup failures clear that
value. Point-only weirs, other assets, timestamps and level history are unchanged.
This is a current derived value and uses the installed model; it does not create
historical storage observations. Its downgrade also requires the backup.

Follow the [operations runbook](../../../docs/private_farm_ops.md#polygon-weir-rollout)
for a coordinated backend/Android update, backup, restored-copy rehearsal and
rollback. The [mobile API](../../../docs/mobile_api.md#weir-percentages-and-storage)
documents typed fields and offline compatibility.

## Validation

The focused Python tests cover polygon import/identity, topology, numerical
integration, typed API/mobile recording, history migration/rollback, analytics
and supply behavior. Run `test_weir_volume.py`, `test_weir_geometry_defaults.py`, `test_polygon_weirs.py`,
`test_weir_percentage_migration.py`, `test_water_network.py`,
`test_water_overflowing.py`, `test_water_asset_detail.py` and `test_weir_overflowing_migration.py` with pytest.
Android checks use `testDebugUnitTest assembleDebug`.

`tests/browser/weir.cjs` uses the existing Playwright installation via `NODE_PATH`
and an isolated in-memory Flask server. Cache Leaflet 1.9.4's `leaflet.js` and
`leaflet.css` from `https://unpkg.com/leaflet@1.9.4/dist/` under
`sandbox/weir-evidence/vendor/` (or set `LEAFLET_TEST_DIR`). The test serves those
files locally and blocks external requests, including map tiles. Cache the
existing Chart.js 4.4.1 bundle from
`https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js` in the same
directory for the analytics checks. It verifies
suggested geometry (including the curved KML fixture), depth shading at 0/50/100%,
island clipping, stale/failed preview handling, saved-line preservation,
manual geometry drawing, preview/save, asset navigation/editing, focused
analytics with collapse/reopen and mobile sizing, numeric and overflow editors, the bulk form and
the polygon popup, and saves desktop/mobile screenshots under the ignored
`sandbox/weir-evidence/` directory. Real Android device interaction still belongs
in the coordinated rollout.

The full Python run also exposed two unrelated failures, reproduced on the
unchanged repository revision: the calendar recurrence detail test expects a
September date already before today, and the livestock-trade import test expects
sex labels absent from its rendered detail. They are outside the weir change;
neither test is skipped or weakened here.

## Historical weir conversion

Migration `d7e9f1a3b5c7` applies the farm owner's requested retrospective estimate:
the first half of each continuous Full period becomes Overflowing, followed by
Full for the remaining half. These converted intervals are **estimates, not new
observations**. Only assets classified as weirs at migration time are converted;
ground-dam history and current asset states are preserved.

Periods use recorded timestamps, not reading counts. Status and active changes
do not restart a period. Ongoing periods end at one captured UTC migration time
for calculation purposes. No state is inferred before the first history entry.
The midpoint uses microsecond precision, with an odd microsecond retained in the
Full half. Existing midpoint entries are reused; otherwise an `updated` entry
is inserted with the active/status state effective at that instant. Previous
water levels are repaired to keep the timeline consistent. Zero-length and
unrepresentably short periods, and assets lacking history, are counted in the
migration log. Inconsistent or future-dated histories abort the migration.

Before deployment, pause application writes, make a backup using
`scripts/backup_agritrack.py --no-prune`, rehearse on a restored copy, and then
run `flask db upgrade`. Verify the logged cutoff/counts, unchanged current
levels, and database integrity before resuming writes. SQLite acquires a write
lock before reading; the conversion and revision advance commit together.
Repeated upgrades at this revision do not convert periods again.

Rollback deliberately requires the pre-migration backup; downgrade is blocked.
This avoids a permanent migration audit table for a one-time correction, but
cannot selectively reverse the estimate after later observations are recorded.
Do not manually reset the Alembic revision or relabel all Overflowing values:
that could repeat the split or overwrite subsequent observations. Retain the
backup and migration log. If future corrections require selective reversal or
per-reading provenance, revisit this limitation by adding explicit change
provenance and an auditable reversal mechanism.
