# Stock and Reproductive Event Lifecycle

## Stock identity

1. Incoming stock creates a cohort and a stock-ledger entry.
2. A full mob transfer keeps the cohort ID.
3. A partial transfer creates a child cohort, records lineage, and posts paired
   transfer-out/transfer-in ledger entries.
4. A current balance is updated in the same transaction as its ledger entry.

## Female current-state observation

1. The user chooses an exact female balance line and an observed count.
2. The user records reproductive state, expected litter size, lactation state,
   and offspring at foot as independent observations.
3. If the observed count is smaller than the balance, the service creates a
   child cohort for that count. The unselected count keeps its prior state.
4. A `FemaleStatusObservation` and mob event preserve when and why the cached
   current state changed.

This flow records an observation. It does not silently treat a farm procedure
or an expected outcome as an observed animal state.

## Breeding cycle

1. Create a species breeding cycle with an exposure date range and select one
   or more participating farms.
2. Enrol all or part of a female cohort. This records the denominator of
   females exposed to a sire and marks that cohort `with_sire`.
3. If a pregnancy assessment is performed, record one effective observation.
   Counts are partitioned into pregnant, not-pregnant, and unassessed
   populations; expected litter size is stored independently. Parturition may
   still be recorded when no pregnancy assessment was performed.
4. Record the parturition outcome. Maternal cohorts move to `parturated`, while
   live births are posted once to the stock ledger and linked back to the
   observation.
5. Record repeatable marking, weaning, or other offspring assessments, plus
   exceptional observations such as pregnancy loss or offspring reassignment.
6. Close the breeding cycle when data entry is complete.

Pregnancy and parturition corrections append a new observation that supersedes
the prior row; the original remains auditable. A parturition correction cannot
change the already-posted live-birth count. If that stock count was wrong, post
a ledger-backed stock adjustment with its reason before recording the corrected
outcome. Historical corrections do not rewrite later cohort movements.

No lactation or offspring-at-foot state is inferred from parturition litter
counts. Those current states require their own female-state observation.

## Analytics definitions

- Parturition rate = females parturated / females exposed to a sire for
  enrollments with a recorded parturition outcome.
- Expected offspring per expected parturition = expected offspring / females
  assessed pregnant where the expected total was quantified.
- Strong at birth ratio = strong offspring at birth / total births.
- Strong offspring per exposed female = strong offspring at birth / females
  exposed to a sire for enrollments with a recorded birth outcome.

An absent denominator returns no rate rather than zero. Missing outcomes,
unquantified expectations, unassessed pregnancy, and unassessed birth-strength
counts are reported so coverage is visible and missing observations are not
silently treated as zero.
