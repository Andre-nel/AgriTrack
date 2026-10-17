# AgriTrack Architecture

AgriTrack is moving to a Flask/Jinja modular monolith. The goal is to keep one
deployable app and one database while giving each feature a clear home.

## Structure

- `app/core`: shared application infrastructure such as the app factory, module
  registry, health checks, and error handling.
- `app/modules/<domain>`: feature-owned routes, schemas, forms, presenters, and
  module documentation.
- `app/models`, `app/services`, and `app/repositories`: existing shared domain
  code. These can move behind modules gradually when ownership becomes clear.
- `app/modules/legacy_web`: temporary compatibility module for the original
  mixed web blueprint.

## Module Contract

Route-owning modules expose `register(app)` for standalone blueprints. During
the compatibility phase, web modules expose `register_legacy_routes(bp)` so the
app can preserve existing `web.*` endpoint names while the route bodies live in
domain modules. A module should keep:

- HTTP concerns in routes and forms.
- Template payload assembly in presenters.
- Business rules and transactions in services.
- JSON response shape in schemas or serializers.

## Migration Rules

- Keep the test suite green after each extraction.
- Prefer behavior-preserving moves before behavior changes.
- Keep old import paths as shims until all callers use module-owned paths.
- Use Alembic for database changes; restructuring must not silently drop data.
- Extract from `legacy_web` by feature, not by line count.

## Current Status

The app factory now lives in `app/core/factory.py`, and route registration is
driven by `app/core/modules.py`. Existing route files have been moved under
`app/modules/*` with compatibility shims at the old paths.

Extracted from `legacy_web` so far:

- `dashboard.routes`: dashboard page.
- `imports.routes`: farm import and import-review pages.
- `farms.routes`: setup, farm list/delete/detail, and farm-scoped create forms.
- `farms.map_routes`: farm/dashboard map-data pages and KML feature payloads.
- `water.routes`: water workspace pages and water form posts.
- `analytics.routes`: analytics landing, journal, and stock tracking pages.
- `grazing.routes`: grazing management analytics page and chart payload helpers.
- `mobs.routes`: mob detail, stock adjustment, movement, transfer, split, and archive forms.
- `reproduction.routes`: breeding cycles, female exposure, pregnancy and parturition
  observations, offspring checkpoints, and reproductive analytics.
- `paddocks.routes`: paddock detail, carrying capacity, rename, and bulk mob movement forms.
- `wiki.routes`: Markdown-backed private farm knowledge wiki pages.

Second-pass decomposition now started:

- `analytics.forms` and `analytics.presenters`: analytics query parsing,
  journal aggregation, and stock tracking chart payloads.
- `analytics.services`: manual journal entry creation and transaction handling.
- `grazing.forms` and `grazing.presenters`: grazing analytics query parsing,
  filtering, timeline shaping, and chart payloads.
- `mobs.forms`: mob movement, transfer, and split form parsing.
- `mobs.presenters`: mob detail template payload assembly.
- `mobs.services`: stock adjustment and balance-line edit business rules and transactions.

## Farm capacity and inventory history

The grazing module owns the farm LSU history report. `FarmCapacityService`
provides the shared active-paddock capacity calculation and explicit snapshot
recording. Farm/paddock web and API edits, mobile status changes, and completed
farm imports record their final capacity in the caller's transaction. The
paddock API records before the rename service's internal commit when both area
and name are changed. Read paths never create a baseline.

`MobInventoryService` captures one baseline per mob and records registered farm
or active-status transitions. Baseline details aggregate cohorts by animal type
and preserve both current counts and the signed ledger net already included.
The report replays subsequent and backdated ledger entries against this fixed
anchor. This is needed because whole-mob moves change farm ownership without
posting stock transfer entries, while cohort partitions can change balance rows
without changing animal-type totals. Existing ledger rows remain the quantity
source; the new history does not duplicate stock events.

History starts from an observation at migration/creation time. Older stock is
reconstructed only when records support it; old capacity is unavailable. The
report preserves gaps for missing or inconsistent evidence, including conflicting
backdated movement chains. This is a data limitation, not a zero-stock assertion.
Historical records survive mob archival. Deleting a farm removes its owned
history while preserving inventory anchors for surviving mobs; deleted farm
references become null so lost ownership is visible as missing coverage.

New write paths must call the appropriate recorder in the same transaction as
the business change. Explicit calls follow the existing water-history pattern
and avoid hidden ORM hooks or scheduled snapshots. The maintenance obligation is
covered by workflow tests and documented in the grazing module README.

## Animal Cohort Identity

`AnimalGroupType` remains taxonomy: species, breed, sex, and age class.
`AnimalCohort` is the persistent identity of a counted population. A mob balance
therefore points to both a type and, after migration/backfill, a cohort. This
allows two groups with identical taxonomy to retain different reproductive
histories in the same mob.

Partial transfers and reproductive partitions create child cohorts and an
`AnimalCohortLineage` edge. Full transfers keep the cohort ID. Stock ledger
entries also carry the cohort ID, so identity and current state follow stock
through mob transfers, splits, and merges.

Breeding-cycle scope uses `BreedingCycleFarm` memberships rather than a single
farm foreign key. A cycle can therefore enroll eligible cohorts and receive
offspring into mobs from any of its one or more selected farms.

The migration establishes a legacy baseline by creating one cohort per current
mob/type balance and attaching historical ledger rows for that same mob/type to
the baseline cohort. This is a compatibility backfill, not a claim that all
historical animals were one biological population; precise lineage begins with
cohort-aware events recorded after the migration.

Female state is deliberately multi-dimensional:

- reproductive state (`with_sire`, `pregnant`, `not_pregnant`, or `parturated`);
- expected litter size;
- lactation state (`lactating` or `dry`); and
- offspring at foot (`none`, `single`, `twins`, or `multiple`).

These fields are independent. For example, `not_pregnant` is not treated as a
synonym for `dry`, and a parturition litter is not assumed to equal the current
offspring-at-foot count. Dated `FemaleStatusObservation` records preserve the
observation behind changes to the cohort's cached current state.

## Mob counts and cohort operations

General mob stock counts combine all cohorts with the same species, breed, sex
and age class. `StockService.group_summary_rows` supplies this read model, and
`adjust_group_stock` expands a type-level stock change into cohort ledger entries.
The existing `adjust_stock` operation remains the single-cohort posting primitive,
including for reproductive birth observations. Callers own commit and rollback.

Without an explicit cohort selection, reductions and partial movements allocate
whole animals proportionally using largest remainders, with balance ID breaking
ties. This is an allocation assumption recorded in audit notes, not an observed
reproductive fact. Multi-destination splits allocate the total moved pool first,
then distribute that pool in destination request order so rounding cannot overdraw
a cohort. Exact cohort actions remain available on the mob's Manage Cohorts page.

Unspecified stock additions and upward recounts create fresh unrecorded cohorts.
They must not inherit another population's observed state or breeding enrollment.
Known transfers preserve identity/state and create lineage for partial transfers.
Whole-type reclassification creates correction children rather than changing
historical parent types. Female-state caches are cleared on non-female children;
the original observations remain on their historical cohorts.

Each stock operation is atomic. Allocation distributions are updated per animal
type alongside its cohort postings, followed by one grazing-history refresh per
affected mob. No schema migration or destructive cohort consolidation is needed.

Selected-paddock stock adjustments resolve the active open-gate component from
the selected paddock's farm. Incoming heads follow existing gate area weights;
outgoing heads follow the selected type's current counts within that component.
Allocation changes carry the stock operation's timestamp. If a camp is emptied,
the old grazing session is closed and its allocations retained, because deleting
an allocation cascades to its LSU history. Remaining assignments enter a new
session without a movement event; history is refreshed after stock posting.

Existing history limitation outside this adjustment path: reclassification still
rewrites allocations in place. A simultaneous head-count reduction that empties a
camp can therefore delete that camp's allocation history. When this path is next
changed, extend the timestamped session replacement to reclassification and cover
depletion; changing cohort identity rules or rebuilding old history is not needed.

Android retains detailed v1 snapshots for compatibility but shows animal-type
totals. Its offline cohort fragments are provisional: totals and head conservation
are exact, while server-generated identities and any tied cohort allocation are
reconciled from the refreshed snapshot. The server's aggregate-stock capability
is confirmed before syncing aggregate commands; old cohort-specific outbox
commands preserve their original meaning. Deploy the server before Android.

Compatibility follow-up:

- Split the compatibility `web` blueprint into domain blueprints once endpoint naming can change.
