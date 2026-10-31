# Mobs

Owns mob API routes and mob web routes registered through the compatibility
`web` blueprint.

Current entry points:
- `api_routes.py`
- `forms.py`
- `presenters.py`
- `routes.py`
- `services.py`

## Linked tasks

The mob detail page's Linked Tasks card starts folded and shows counts for each
status present in its description. Expanding the card reveals individually
foldable tasks: Closed tasks start folded, and every other status starts open.
Each task's key links to its detail page even while that task is folded. Folding
is local to the page; reloading restores these defaults.

## Balances and cohort management

The Balances card and ordinary adjustment, transfer, split and paddock-allocation
controls show one combined count per species, breed, sex and age class. Cohort
state never creates another ordinary balance row. An absolute count sets the
combined total, including zero; an unchanged count posts no ledger entries.
Reductions and transfers distribute whole animals proportionally across cohorts
with deterministic rounding. Additions create fresh cohorts whose state is not
recorded. Underlying cohort identities and observation history remain intact.

**Manage Cohorts** opens `/mobs/<mob_id>/cohorts`. This page shows each positive
cohort balance, its short identifier and all four state fields. Dated female-state
observations and targeted stock adjustments/transfers live here. These actions
resolve a current positive `balance_id` within the mob; stale, empty or foreign
selections are rejected. Targeted additions retain the selected cohort's state.
Legacy balances receive a cohort only when a mutation needs one, never on a page
read or an unchanged count.

The existing female-status POST route redirects back to cohort management.
Cohort stock forms use `/cohorts/adjust` and `/cohorts/transfer` under the mob URL
and share stock services with ordinary controls.

## Reclassifying animals

Select a balance line in the mob detail page, then use **Reclassify animals** to
change the sex or age class of some or all of its animals. Species and breed stay
fixed. Enter a whole-head quantity or use **All animals**; the preview shows the
source remainder and the destination total, including animals already classified
there. The mob's total head count stays unchanged. Use **Adjust Stock** for count
corrections; selecting a line also populates that form.

`POST /mobs/<mob_id>/balances/reclassify` accepts
`source_animal_group_type_id`, `quantity`, `sex`, `age_class` and an optional
`note`. It rejects unchanged classifications and quantities outside the current
source balance. The obsolete `/balances/edit` route only asks the user to refresh
and never interprets an old final head count as a reclassification quantity.

Reclassification posts at the current time in one transaction. Whole animals are
allocated proportionally across the source cohorts with deterministic rounding;
the audit notes record this allocation assumption. Reclassified shares become
correction children. Remaining parents, historical observations and populations
in other mobs are preserved. Female children inherit female state; non-female
children clear those cached fields. Existing destination cohorts remain intact.

The same quantity is removed and added within each source paddock, preserving its
total animals and unrelated stock. Legacy percentage allocations are normalized
with whole-head rounding; if this removes an empty paddock, a shared session
replacement helper retains its historical allocation records. Equal outgoing and
incoming ledger postings and one mob event share a timestamp. Inventory baselines
are captured before mutation, and grazing history refreshes after all postings so
the classification and LSU change starts at that timestamp. Failures roll back the
entire operation. This web form requires no REST/mobile schema or database change.

## Transfers to existing mobs

The Transfer Stock To Existing Mob card accepts multiple rows, each with its own
farm, destination mob, animal group, whole-head quantity and optional note. Each
row's mob choices follow its selected farm and exclude the source and inactive
mobs. Rows can repeat a group or destination; their notes remain separate.

All rows post in one transaction. Invalid destinations, incomplete rows or a
combined quantity exceeding the source's available stock reject the entire
submission, including any ledger, cohort, event and grazing-history changes.
Each row uses the existing proportional cohort transfer rules against the stock
remaining after preceding rows. Reload an older open transfer form before use;
the submitted destination and note fields must now be present for every row.

## Stock adjustments and open gates

In Adjust Stock, a selected paddock identifies its whole active open-gate network,
including transitive connections and connections across farms. Every stock event
uses this same rule. Additions distribute only the new animals by the existing
gate area weights (grazeable area, otherwise total area; equal shares if all areas
are zero). Reductions use the selected animal type's existing headcounts within
that network as proportional weights. Both use deterministic whole-head rounding.
An insufficient network total rejects the entire adjustment, even if disconnected
paddocks hold enough animals. Other types, mobs and disconnected paddocks are not
redistributed. Without an open connection, the selected paddock acts alone.

Count continues to set the animal type's combined total across the mob (or the
selected cohort in cohort management); only its resulting delta applies to the
chosen paddock network. All current paddocks retains its existing weighting.

When a stock reduction empties a paddock, its grazing allocation is retained in a
closed session so past history survives. A replacement session holds the remaining
allocations at the stock event's timestamp; retained paddocks keep their continuous
grazing days. Removing all animals closes the session without opening an empty one.
History is synchronized after the complete stock posting, with no synthetic move.
An adjustment that empties a camp cannot predate its current grazing session or
open history intervals; it is rejected rather than overwriting later evidence.

## Stock count history

The mob detail page links to `/mobs/<mob_id>/stock-history` for an active mob.
The page uses the shared analytics stock chart and supports date and animal-type
filters, grouping by animal characteristics, and a single total when all grouping
fields are cleared. It retains the mob's full ledger history across farm moves;
farm filters and farm grouping are not offered on this page.

The history chart shows each timestamped stock change in UTC with its original
ledger notes on hover or selection. Simultaneous entries in the same plotted
group remain together. Zoom, pan, reset, and legend toggles allow closer inspection.
Missing-stock events and notes matching configured whole words, including `weg`,
receive red diamond markers, including simultaneous changes whose net count is
zero. These highlights do not change any records.

Counts before a selected start date establish the opening balance. Views ending
today or later reconcile to this mob's current balance, including zero stock;
any opening offset is separate from recorded events and notes.
