# Water

Owns water network API routes, web routes registered through the compatibility
`web` blueprint, and water form parsing.

Current entry points:
- `api_routes.py`
- `forms.py`
- `routes.py`

## Water levels

`WaterNetworkService.water_level_options_for_type` defines the recording choices.
Weirs and ground dams support Empty, Low, Half, High, Full, and Overflowing
(`overflowing`). Other water-level assets retain the original five choices.
Web editors, bulk forms, imports, and mobile commands use the same validation.
Changing an overflowing asset to an incompatible type requires an explicit
replacement level (or clearing the level). An operational trough supplied by an
overflowing source displays Full; its reported level remains unchanged.

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
