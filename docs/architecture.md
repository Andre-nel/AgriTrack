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

Female state is deliberately multi-dimensional:

- reproductive state (`with_sire`, `pregnant`, `not_pregnant`, or `parturated`);
- expected litter size;
- lactation state (`lactating` or `dry`); and
- offspring at foot (`none`, `single`, `twins`, or `multiple`).

These fields are independent. For example, `not_pregnant` is not treated as a
synonym for `dry`, and a parturition litter is not assumed to equal the current
offspring-at-foot count. Dated `FemaleStatusObservation` records preserve the
observation behind changes to the cohort's cached current state.

Compatibility follow-up:

- Split the compatibility `web` blueprint into domain blueprints once endpoint naming can change.
