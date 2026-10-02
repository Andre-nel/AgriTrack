# Reproduction

Owns cohort-based breeding cycles, sire exposure, pregnancy observations,
parturition outcomes, later offspring assessments, and reproductive analytics.
Each cycle has one or more participating farms through `BreedingCycleFarm`.

Stock identity remains in persistent animal cohorts. Reproductive observations
are recorded separately from stock procedures; live births are the only outcome
automatically posted to stock.

## State model

Female cohort state is orthogonal: reproductive state, expected litter size,
lactation state, and offspring at foot are separate fields. A dated status form
on the mob's **Manage Cohorts** page (`/mobs/<mob_id>/cohorts`) can split a subset
of a balance and records a
`FemaleStatusObservation`. Parturition does not infer lactation or offspring at
foot.

Ordinary mob counts combine all cohorts of the same species, breed, sex and age
class. Proportional stock reductions and movements preserve cohort state and
lineage without creating reproductive observations. Generic additions have state
not recorded; a targeted addition on the cohort page retains the selected state.

## Analytics

The report exposes females exposed, assessed pregnant, females parturated,
expected offspring, total births, strong at birth, and assessment coverage. The
primary rates use explicit denominators and return no value when the denominator
is zero.

## Data ownership

- `AnimalCohort` and stock ledger entries own stock identity.
- `BreedingCycle` and child observation tables own reproductive facts.
- `ParturitionStockEntry` links a birth observation to the posted stock entry.
- The cohort's state fields are a current-state cache, not a replacement for
  dated observations.
