# Reproductive Cohort Data Model

The existing mob/type balance remains the current stock read model. Cohort
identity sits between stock and reproductive observations so stock movement
does not erase history.

```text
Farm 1---* Mob 1---* AnimalGroupBalance *---1 AnimalGroupType
                  |                      |
                  *---1 AnimalCohort 1---* StockLedgerEntry
                            |
                            *---* AnimalCohortLineage (parent -> child)
                            |
                            *---* FemaleStatusObservation

Farm *---* BreedingCycle 1---* BreedingEnrollment *---1 AnimalCohort
           (via BreedingCycleFarm)        |
                                          +---* PregnancyAssessment
                                          +---* ParturitionOutcome 1---* ParturitionStockEntry
                                          +---* OffspringAssessment
                                          +---* ReproductiveException
```

Key constraints:

- a balance is unique by `(mob_id, cohort_id)`;
- legacy rows without a cohort remain temporarily unique by mob and animal type;
- a cycle has one or more distinct farm memberships;
- an enrollment count is positive and belongs to one cycle/cohort pair;
- observed count fields are non-negative and validated against their parent
  enrollment by the service layer;
- a live-birth stock ledger entry can be linked to only one parturition outcome;
- observation rows can supersede or be voided without overwriting the original
  observation.

`ParturitionOutcome` stores birth facts. `AnimalCohort.offspring_at_foot` stores
current female-cohort state. They are not derived from each other because
stillbirth, later loss, fostering, or reassignment can make those counts differ.
