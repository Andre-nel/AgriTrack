# Imports

Owns farm import and import-review web routes, KML upload request handling, and
import presenters.

Current entry points:
- `routes.py`

## Polygon weirs

WR polygons are classified before paddocks. They never contribute paddock or
grazing area and appear in the water layer. Point-only WR placemarks continue to
work. For a same-name point and polygon, the polygon wins regardless of order;
the point supplies an initial wall hint. Polygon holes are supported; disconnected,
self-intersecting or malformed outlines are rejected.

Within the same farm, normalized case-insensitive imported names take precedence,
then unambiguous asset names. A match upgrades the asset in place, preserving
its ID, history, notes, connections and operational state. Ambiguous existing
matches or conflicting same-name polygons abort the import with an actionable
error. Repeated imports and later point-only imports preserve polygon geometry.
Confirmed centreline/wall configuration survives unchanged reimports. A changed
outline pauses estimates until reviewed in the geometry setup screen.

Full-farm imports retain their existing paddock, gate, fence and other water
asset synchronization behavior. Rehearse the complete KML on a restored database
and inspect the summary before applying it to live data. The supplied Rietfontein
example yields 41 paddock polygons; Huiskamp Boonste Weir is one water asset, not
a paddock. See the [water model](../water/README.md) for setup and assumptions.
