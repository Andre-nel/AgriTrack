# Orchards

The web workspace records orchard layout, individual plants, planting plans and
schematic irrigation. Open **Orchards** in the main navigation or **Open Orchards**
on a farm page. It works in desktop and phone browsers; it is not part of the
native Android offline snapshot.

## Working with the layout

The map uses local metres, with positive x to the right and positive y upwards.
It has no GPS origin or assigned compass direction. Leaflet's `CRS.Simple` is
used only for drawing and interaction; coordinates are stored as x/y metres,
not latitude/longitude. Fit layout shows the overview. Zoom in for labels and
individual selection, or use the **Inspect orchard** position list.

Add a horizontal row or vertical column with an origin, spacing and space count.
Defaults are 4 m horizontally and 3 m vertically. New spaces are empty. The row
editor also extends or trims either end. An origin is a stable reference for
position index zero, not necessarily the first space after an extension. Signed
indices preserve existing space IDs and coordinates when extending the start.
The display numbers spaces in their current order; display numbers are not IDs.

Row movements and orientation/spacing changes correct the recorded layout. They
do not assert a physical transplant and do not move independently drawn irrigation.
Occupied rows or end spaces cannot be deleted or trimmed. Reassign planned plants
or explicitly correct planted locations first. Deleting a row removes its empty
spaces; its independent irrigation lines remain until explicitly removed.

Select a row/column name on the map, or **Move name label** in its row editor,
to reposition the name independently. Drag the label, pick a point on the map,
or enter label coordinates, then **Save label position**. Custom positions stay
fixed when row geometry changes. **Reset to default**, followed by save, restores
automatic positioning to the left of the first space. Discard restores the last
saved position. Both horizontal rows and vertical columns support this workflow.

## Stock and planting

Plants retain individual UUIDs even when stock is grouped by name in the UI.
`unassigned` has no space; `planned` reserves one space; `planted` occupies one
space. A plant can have at most one space and a space at most one plant. Planning
reduces available stock without increasing planted counts. Cancelling a plan
releases stock and the space. Confirmation records a planting date; unknown
historical planting dates stay null. Correcting a planted location preserves the
plant's identity, purchase metadata and planting date.

**Remove 1** in Plant stock removes one available plant, with its saved name and
purchase details shown for confirmation. Individual unplanted plant details also
offer **Remove from stock**. Removing a planned plant frees its reserved space;
cancelling a reservation instead returns that plant to available stock. Planted
trees are protected by the server as well as the UI. Removal permanently deletes
the selected individual record and its purchase metadata; it is not a lifecycle
event or accounting transaction. Other plants with the same name are preserved.

Plant names are supplied observations, not inferred taxonomy. For generic invoice
names, varieties remain unspecified. The workspace also supports grape and berry
plants. It does not make suitability, spacing or agricultural recommendations.
Purchase fields describe provenance; they do not post accounting transactions.
This version records current placement and purchase/planting dates, not a full
plant lifecycle or transplant-event history.

## Irrigation

Draw a line with map clicks/taps or enter one `x, y` pair per text line. Finish
drawing, review, then save. Edit vertices by dragging handles or changing/removing
coordinate lines. Emitters are drippers or sprinklers attached to one line.
Click/tap or enter their positions; they snap to the nearest point on that line.

When reshaping a line, existing emitter positions are projected to the nearest
point on the proposed polyline and shown in the preview before saving. Ties use
the first segment. The server repeats this calculation in the same transaction
as the line change. Deleting a line explicitly confirms removal of its emitters.
Seed routes are dashed and marked schematic until verified by the user. No emitter
positions are inferred. There is no water-source linkage or hydraulic calculation.

## HTTP and data ownership

The module registers its own `orchards` blueprint. Routes handle HTTP and
transactions; `services.py` owns business rules, `validation.py` owns scalar and
geometry parsing, and `schemas.py` assembles the saved state. `app/models/orchard.py`
contains the six relational entities. Irrigation vertices are a JSON array of
`[x, y]` pairs; plants and spaces are individual relational records.

The shared web session authentication protects both pages and JSON endpoints.
All child IDs are checked against the orchard, and the orchard against the farm.
The JSON base URL is `/api/farms/<farm_id>/orchards/<orchard_id>`:

| Method and suffix | Behaviour |
| --- | --- |
| `GET` base URL | Full saved layout, stock, irrigation, counts and revision |
| `POST /rows` | Create a row and its empty spaces |
| `PATCH /rows/<id>` | Correct geometry/name or label position; optionally resize atomically |
| `POST /rows/<id>/resize` | Extend or trim `start` or `end` |
| `DELETE /rows/<id>` | Remove an entirely empty row |
| `POST /plants` | Add individual unassigned plants by quantity |
| `PATCH /plants/<id>` | Correct a plant name |
| `DELETE /plants/<id>` | Remove one unassigned/planned plant; release any reservation |
| `POST /plants/<id>/placement` | `reserve`, `cancel`, `plant` or `correct` |
| `POST /lines`, `PATCH /lines/<id>`, `DELETE /lines/<id>` | Manage routes |
| `POST /lines/<id>/emitters` | Place an attached emitter |
| `PATCH /emitters/<id>`, `DELETE /emitters/<id>` | Move/edit or remove an emitter |

`POST /api/farms/<farm_id>/orchards` creates an empty named orchard. Every mutation
of an existing orchard requires its integer `revision` in the JSON object. A
conditional database update claims that revision before reading child records.
Changes and the revision increment commit together. Stale requests return 409;
invalid input returns 400; a SQLite lock returns a retryable 503. Successful
mutations return the complete saved snapshot. Validation failures roll back the
revision as well as data changes. No counters are separately maintained.

Rows expose nullable `label_x_m` and `label_y_m`. A label update must supply both
finite coordinates within the coordinate bounds, or both null for automatic
positioning. Omitting both preserves the existing setting. Label-only updates
leave the row origin, spacing, spaces, plants and irrigation unchanged.

Failed saves retain the form and preview. A conflict requires an explicit refresh;
refresh/navigation asks before discarding unsaved edits. Requests whose responses
are lost may have committed, so refresh before retrying after a revision conflict.
Coordinate values must be finite and within ±1,000,000 m, spacing positive, rows
at most 1,000 spaces, stock additions at most 1,000 plants and lines 2–200 vertices.
Adjacent duplicate vertices are rejected. These are input bounds, not farm advice.

Occupied-space foreign keys restrict deletion. Farm deletion explicitly removes
owned plants first; database cascades remove orchard rows, spaces, lines and
emitters. Nothing is linked to livestock or water-network entities.

## Initial observation and deployment

`fixtures/rietfontein_v1.json` preserves the owner's supplied names and row order.
The owner confirmed 3 m row spacing, 4 m plant spacing, and that the leading empty
spaces of rows 7 and 8 align with column 5. `null` represents each supplied `None`
space. The result is 80 spaces, 69 planted trees and 11 empty spaces. Row 1 starts
at `(0, 0)`; rows 7 and 8 start at `(16, 18)` and `(16, 21)`.

The attached invoice from The Pot & Barrel dated 21 October 2026 supplies 23
unplanted plants: 3 apricots, 2 apples, 1 nectarine, 1 plum, 2 peaches, 1 blackberry,
1 strawberry, 4 pomegranates and 8 grapes. The two bags of mulch are excluded.
The fixture records source provenance; the temporary clipboard image is not a
persistent attachment. Unknown varieties and historical planting dates remain
unknown. There are no new veterinary claims or changes to farm protocols.

The additive migration is `e8a1c3d5f709`, following `d7e9f1a3b5c7`. Back up with
`python scripts/backup_agritrack.py --no-prune`, rehearse on an isolated database,
then run `flask db upgrade` followed by `flask import-orchard --farm Rietfontein`.
Schema migration does not import observations. The named import is atomic and
uses a unique `(farm_id, seed_key)` to make repetition non-destructive. It skips
an existing import without resetting subsequent edits. New databases can also
create empty orchards through the UI. Database backups already include all new
tables; no extra attachment directory is introduced.

Existing orchard installations also need migration `f9b2d4e6a810`, which adds
nullable label coordinates without modifying existing plant or layout records.
Back up first, then run `flask db upgrade`. Downgrading only this migration
preserves orchard records but loses custom label positions.

Downgrading `e8a1c3d5f709` removes orchard records. Restore a backup
when orchard data must be retained. Do not downgrade the earlier weir-history
migration as part of orchard rollback.

## Verification

- `python -m pytest tests/integration/test_orchards.py -q` covers the import,
  ownership, revision conflicts, atomic validation, placement, stock removal and
  planted-tree protection, independent labels, geometry, irrigation, authentication,
  migration/schema parity, existing-orchard migration and farm deletion.
- `node tests/browser/orchard.cjs` starts an isolated in-memory Flask server and
  exercises actual persistence, desktop/phone controls, stock removal, label
  dragging/picking/reset, discard and failed saves. Set
  `NODE_PATH` to the existing Playwright installation and, when needed,
  `ORCHARD_PYTHON` and `CHROME_PATH`. Screenshots go under ignored
  `sandbox/orchard-evidence`.
- Run the existing regression suite and `python scripts/validate_wiki.py`; that
  validator includes wiki link checking. No wiki pages are created by this module.

The existing Leaflet CDN dependency is retained. The map requires that asset to
load; an explicit error is shown if it is unavailable. Offline/native Android
editing, audited lifecycle events, GPS placement and water-source integration
would be separate features rather than implicit claims of this version.
