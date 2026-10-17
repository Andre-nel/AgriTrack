# Finance

Owns cash-flow and finance web routes.

Current entry points:
- `routes.py`

## Livestock trade imports

Open **Cash Flow → Import Livestock Trade** (also available from Record Livestock
Trade and Trade Statistics). Download a fresh JSON template and the accompanying
[ChatGPT instructions](import_instructions.txt), give both to ChatGPT with the
photographs, then paste or upload the completed JSON. Close-ups must clarify the
original pages, not add animals a second time. AgriTrack stores optional reference
images with the saved trade but does not interpret them. No OCR service or external
AI account is required by the app.

Use **Generate import ID** and **Copy** when only a fresh `import_id` is needed;
this does not create a trade or change the JSON already entered. Put the copied
ID in the document and retain it for retries of the same trade.

Attach reference images on either the initial import form or the preview. JPEG,
PNG, WebP, HEIC and HEIF are accepted, up to 12 MiB each and 10 images per trade.
The server checks file signatures instead of trusting the filename or supplied
MIME type. JPEG, PNG and WebP can be viewed inline; HEIC/HEIF are downloadable for
a compatible viewer. Existing images survive previews and validation errors.
Select **Remove** and preview again to remove an image. Changes to images, like
changes to trade details, require a fresh preview before confirmation.

**Discard trade** removes the referenced temporary images and returns to the
import form without creating a trade, animal types or cashflow. Saved trades show
their reference images on the detail page. Deleting a saved trade also removes
its image records and files after the database commit succeeds.

The review screen displays every weight and calculates group, sex, and combined
statistics. Select the farm(s) and complete missing descriptive information before
saving. A selected existing animal type can supply breed, sex and age; conflicting
explicit details are rejected. Unknown sex is unresolved, not automatically mixed.
If you change anything after preview, preview again before saving. The server also
checks a signed, one-hour preview token so bypassing the disabled button cannot
save different, unreviewed values.

### Version 1 format

One document represents one sale or purchase, one species (`Cattle`, `Sheep`, or
`Goat`), and one pricing method (`weight` or `head`). Rates exclude VAT; currency
is rand. Each group can have its own price and animal type. There can be several
groups of the same sex. Group labels are descriptive, not a source for inferred
sex or age.

Example with illustrative data (download a fresh template for its unique ID):

```json
{
  "schema_version": 1,
  "import_id": "c20db4b1-e05c-44b6-9136-37222f01b515",
  "trade_type": "sale",
  "date": "2026-10-15",
  "counterparty": "Example Buyer",
  "species": "Cattle",
  "pricing_model": "weight",
  "vat_percent": 15,
  "reference": "Example invoice",
  "notes": "Illustrative data only",
  "groups": [
    {
      "label": "Heifers",
      "breed": "Bonsmara",
      "sex": "female",
      "age_class": "young",
      "weights_kg": [250, 260],
      "price_per_kg": 40
    },
    {
      "label": "Bull calves",
      "breed": "Bonsmara",
      "sex": "male",
      "age_class": "calf",
      "weights_kg": [280, 300],
      "price_per_kg": 45
    }
  ]
}
```

For per-head pricing, set `pricing_model` to `head` and replace each group's
`weights_kg` and `price_per_kg` with `head_count` and `price_per_head`. Goat groups
priced per head also require `hair_lengths`, retaining the existing farm workflow.
Optional group fields are `notes` and `label`. The editor can also supply an
`animal_group_type_id`; handwritten-photo transcriptions do not need database IDs.
Farms are selected separately in the review UI, retaining the existing single-farm
or business-wide cashflow attribution for trades linked to multiple farms.

Public sex values map explicitly to the existing animal taxonomy:

| Input | Cattle | Sheep / Goat |
|---|---|---|
| female | cow | ewe |
| male | bul | ram |
| castrated | ox | wether |
| mixed | mixed | mixed |

Existing taxonomy codes remain accepted for compatibility. Age classes use the
existing species-specific options. Breed, sex, age, date and counterparty can be
incomplete while reviewing, but are mandatory before saving. An absent VAT value
uses the visible existing default of 15%; `0` means zero VAT and `0.15` means
**0.15%**, not 15%, in the versioned document and grouped editor. Only the old form
adapter retains its historical fractional-rate convention.

Numbers may be JSON numbers or decimal strings, with decimal points and no
currency/thousands separators. Each weight must be positive, finite and resolved;
null/blank weights are errors, not omitted animals. Repeated weights are retained.
Duplicate JSON keys, unknown fields, unsupported versions, conflicting pricing
fields and mixed-species groups are rejected. Do not supply calculated totals.
Input documents are limited to 2 MiB and 1,000 groups. There is no 12-animal limit.

### Calculations and persistence

`livestock_trade_payload` performs read-only parsing, normalization and validation.
`livestock_trade_calculations` calculates normalized data with `Decimal`; preview,
save and reports use the same calculations. Weights round half-up to 0.001 kg,
R/kg rates to 0.0001, and monetary amounts to cents. VAT percentages support two
decimal places. Values that round to zero are rejected for required positive fields.

- Weighted group subtotal = total kg × R/kg, rounded to cents.
- Per-head group subtotal = count × R/head, rounded to cents.
- Each group's VAT = rounded subtotal × VAT rate, rounded to cents.
- Trade and sex subtotals, VAT and gross amounts are sums of the group amounts.
  This intentionally keeps the displayed breakdown reconcilable; it can differ
  by a cent from applying VAT once to the entire multi-group subtotal.
- Average kg = total kg ÷ count; average price/head = subtotal ÷ count.
- Combined R/kg = subtotal ÷ total kg. Combined R/head = subtotal ÷ count.
  Group rates remain the entered rates. Averages are never averages of averages.
- Per-head trades show N/A for weight statistics. Price charts distinguish R/kg
  and R/head even when the same species uses both in different trades.

`LivestockTrade` owns the header, farms and cashflow link. `LivestockTradeGroup`
owns animal taxonomy and pricing; each `LivestockTradeWeight` belongs to a group.
Validation finishes before types or financial records are written. Import
confirmation owns one database commit, including image metadata; manual
create/edit/delete retain their existing route-owned commit/rollback. Preview
creates no database records. Temporary image files live in the private instance
directory, with signed references bound to the browser session and a 24-hour
expiry. New uploads clean up expired files in bounded batches; abandoned files
can remain on disk until a later upload. No image bytes enter session cookies.
Confirmation copies images to `instance/livestock_trade_attachments` before
committing; failed saves and concurrent losers remove their newly copied files.
Temporary images remain available for identical save retries until expiry or
explicit discard. Saved images are included in the standard backup zip.

Storage follows the app's existing file-plus-metadata approach, keeping large
images out of SQLite and avoiding a new storage service or background worker.
Database rollback and caught file errors are covered; an abrupt process or machine
failure between copying a file and committing metadata can leave an unreferenced
file. This costs disk space but does not create a partial trade. Back up before
reconciling files against `livestock_trade_attachments.storage_path`. If orphan
files accumulate after crashes, add an explicit reconciliation command with a
dry-run report and grace period. Database blobs or a transactional storage system
would avoid this gap at the cost of larger databases or new infrastructure.

The resulting cashflow line uses the VAT-inclusive total and trade date, existing
species category, and sale/purchase direction. Edit updates the same cash
transaction; delete removes it and all owned groups/weights. Financial trade
recording does not post inventory ledger entries or change mob counts.

The generated `import_id` is a database-enforced unique key. Its stored hash covers
the completed, normalized payload, selected farms and, when attached, the ordered
image fingerprints (content hash, filename, type and size). Identical retries return the
existing trade; a changed payload with that ID is rejected and must be edited via
the trade detail page. Concurrent retries roll back the losing transaction before
returning the existing trade. Subsequent edits do not replace the original import
hash. Duplicate protection is by ID, not by similarity: starting a new template
for an already-recorded trade produces a new ID and must be avoided. Deleting a
trade releases its import ID.

### Migration and verification

Migration `b4c6d8e0f213`, following `fa2b4c6d8e10`, converts each existing trade to
one group and preserves trade IDs, cash transactions, farm links, individual
weight IDs/order, values and timestamps. It does not repost or recalculate cashflow.
Superseded header pricing/type columns are removed. SQLite table replacement runs
inside an explicit transaction with foreign-key enforcement enabled and a final
foreign-key check. Back up before applying it to farm data using the existing
[operations runbook](../../../docs/private_farm_ops.md).

Downgrade restores representable legacy trades, but refuses imported/multi-group
trades, group labels/notes or pricing modes the old schema cannot represent.
Restore the pre-upgrade backup rather than discard these records. Stop the running
app during migration and restart it with the matching application version.

Migration `c6d8e0f213a5` follows the grouped-trade migration and adds reference-image
metadata without rewriting existing trades or cashflow. Run `flask db upgrade`
before using the updated application. Downgrade refuses to discard saved image
metadata; restore a suitable backup if returning to a version without attachments.

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_livestock_trade_service.py tests/integration/test_cash_flow.py tests/integration/test_livestock_trade_import.py tests/integration/test_livestock_trade_migration.py tests/integration/test_livestock_trade_images.py tests/integration/test_livestock_trade_image_migration.py tests/integration/test_private_farm_hardening.py
.\.venv\Scripts\python.exe scripts/validate_wiki.py
```

The migration tests use disposable databases and check both empty and populated
upgrades, round trips, unchanged cashflow, foreign keys and lossy-downgrade refusal.
Import tests cover mixed prices/sexes, per-head trades, group VAT rounding, invalid
input, read-only preview, rollback, duplicate/concurrent submissions, authentication,
and preservation of 210 weights through editing.

This documents application behavior, not agricultural or veterinary claims.

