"""Read-only validation shared by manual entry, import preview, and persistence.

No animal types or other records are created here. A preview may omit descriptive
details, but never drops an unresolved weight or guesses an animal's sex.
"""

import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import UUID, uuid4

from app.extensions import db
from app.models import AnimalGroupType, Farm
from app.services.stock_service import StockService

MONEY_QUANT = Decimal("0.01")
RATE_QUANT = Decimal("0.0001")
WEIGHT_QUANT = Decimal("0.001")
VAT_QUANT = Decimal("0.0001")
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
SEX_CODES = {
    "Cattle": {"female": "cow", "male": "bul", "castrated": "ox", "mixed": "mixed"},
    "Sheep": {"female": "ewe", "male": "ram", "castrated": "wether", "mixed": "mixed"},
    "Goat": {"female": "ewe", "male": "ram", "castrated": "wether", "mixed": "mixed"},
}
HEADER_FIELDS = {
    "schema_version", "import_id", "trade_type", "date", "counterparty", "species",
    "pricing_model", "vat_percent", "reference", "notes", "groups",
}
GROUP_FIELDS = {
    "label", "breed", "sex", "age_class", "animal_group_type_id", "notes",
    "weights_kg", "price_per_kg", "head_count", "price_per_head", "hair_lengths",
}


def text(value, name, limit, *, required=False):
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    value = (value or "").strip()
    if required and not value:
        raise ValueError(f"{name} is required")
    if len(value) > limit:
        raise ValueError(f"{name} must be {limit} characters or fewer")
    return value or None


def decimal_value(value, name, quant, *, allow_zero=False, limit=Decimal("10000000000")):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError(f"{name} must be a number; unresolved values cannot be saved")
    try:
        number = Decimal(str(value).strip())
        if not number.is_finite():
            raise ValueError(f"{name} must be finite")
        if number < 0:
            raise ValueError(f"{name} must not be negative")
        number = number.quantize(quant, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number within the supported precision") from exc
    if number < 0 or (not allow_zero and number == 0) or number >= limit:
        comparison = "zero or greater" if allow_zero else "greater than zero"
        raise ValueError(f"{name} must be {comparison} and less than {limit}")
    return number


def positive_int(value, name):
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
        raise ValueError(f"{name} must be a positive whole number")
    result = int(value)
    if not 0 < result <= 2147483647:
        raise ValueError(f"{name} must be between 1 and 2147483647")
    return result


def public_sex(species, value):
    return {code: label for label, code in SEX_CODES[species].items()}.get(value, value)


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError(f"Invalid number: {value}")


def check_shape(document):
    if not isinstance(document, dict):
        raise ValueError("Import one trade as a JSON object")
    if type(document.get("schema_version")) is not int or document["schema_version"] != 1:
        raise ValueError("Unsupported schema_version; expected 1")
    unknown = document.keys() - HEADER_FIELDS
    if unknown:
        raise ValueError("Unknown trade fields: " + ", ".join(sorted(unknown)))
    groups = document.get("groups")
    if not isinstance(groups, list) or not groups or not all(isinstance(g, dict) for g in groups):
        raise ValueError("groups must be a non-empty array of group objects")
    if len(groups) > 1000:
        raise ValueError("A trade can contain at most 1000 groups")
    for index, group in enumerate(groups, 1):
        unknown = group.keys() - GROUP_FIELDS
        if unknown:
            raise ValueError(f"Group {index}: unknown fields: {', '.join(sorted(unknown))}. Species and pricing method belong to the trade.")


def load_document(raw):
    if len(raw.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise ValueError("The JSON document must be 2 MiB or smaller")
    try:
        document = json.loads(raw, parse_float=Decimal, object_pairs_hook=_unique_keys,
                              parse_constant=_reject_constant)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc
    check_shape(document)
    return document


def normalize_document(document, farm_ids, *, require_details=True, require_import_id=True):
    check_shape(document)
    import_id = document.get("import_id")
    if import_id or require_import_id:
        try:
            import_id = str(UUID(str(import_id)))
        except (ValueError, AttributeError) as exc:
            raise ValueError("import_id must be a UUID; download a fresh template for a new trade") from exc
    else:
        import_id = None
    trade_type = text(document.get("trade_type"), "Trade type", 20, required=True)
    if trade_type not in ("sale", "purchase"):
        raise ValueError("Trade type must be sale or purchase")
    species = StockService.normalize_species(text(document.get("species"), "Species", 20, required=True))
    pricing = document.get("pricing_model")
    if pricing not in ("weight", "head"):
        raise ValueError("Pricing method must be weight or head")
    date_text = text(document.get("date"), "Trade date", 10, required=require_details)
    try:
        trade_date = date.fromisoformat(date_text) if date_text else None
    except ValueError as exc:
        raise ValueError("Trade date must be valid (YYYY-MM-DD)") from exc
    vat_percent = document.get("vat_percent")
    vat_percent = 15 if vat_percent in (None, "") else vat_percent
    percent = decimal_value(vat_percent, "VAT percentage", Decimal("0.01"), allow_zero=True, limit=Decimal("100.01"))
    farm_ids = sorted({str(value) for value in farm_ids if value})
    if require_details and not farm_ids:
        raise ValueError("At least one farm is required")
    if farm_ids and Farm.query.filter(Farm.id.in_(farm_ids)).count() != len(farm_ids):
        raise ValueError("Selected farm is invalid")
    result = {
        "import_id": import_id, "trade_type": trade_type, "trade_date": trade_date,
        "counterparty": text(document.get("counterparty"), "Buyer/seller", 120, required=require_details),
        "reference": text(document.get("reference"), "Reference", 120),
        "notes": text(document.get("notes"), "Notes", 2000),
        "species": species, "pricing_model": pricing, "vat_rate": percent / 100,
        "farm_ids": farm_ids, "groups": [],
    }
    for index, raw_group in enumerate(document["groups"], 1):
        try:
            result["groups"].append(_normalize_group(raw_group, species, pricing, require_details))
        except ValueError as exc:
            raise ValueError(f"Group {index}: {exc}") from exc
    return result


def _normalize_group(raw, species, pricing, require_details):
    group = {key: text(raw.get(key), name, limit) for key, name, limit in (
        ("label", "Label", 120), ("notes", "Notes", 2000), ("hair_lengths", "Hair lengths", 250),
    )}
    breed = text(raw.get("breed"), "Breed", 50)
    breed = " ".join(breed.split()) if breed else None
    sex = text(raw.get("sex"), "Sex", 20)
    if sex:
        sex = SEX_CODES[species].get(sex.lower(), sex.lower())
        sex = StockService.normalize_sex(species, sex)
    age = text(raw.get("age_class"), "Age class", 50)
    if age:
        age = StockService.normalize_age_class(species, age)
    type_id = text(raw.get("animal_group_type_id"), "Animal type", 36)
    if type_id:
        existing = db.session.get(AnimalGroupType, type_id)
        if existing is None or existing.species != species:
            raise ValueError("Animal type must exist and match the trade species")
        for supplied, actual, field in ((breed, existing.breed, "Breed"), (sex, existing.sex, "Sex"), (age, existing.age_class, "Age class")):
            if supplied and supplied != actual:
                raise ValueError(f"{field} conflicts with the selected animal type")
        breed, sex, age = existing.breed, existing.sex, existing.age_class
    if require_details:
        for value, field in ((breed, "Breed"), (sex, "Sex"), (age, "Age class")):
            if not value:
                raise ValueError(f"{field} is required; choose an animal type or complete its fields")
    group.update(breed=breed, sex=sex, age_class=age)
    group.update(weights=[], price_per_kg=None, price_per_head=None, head_count=None)
    if pricing == "weight":
        if raw.get("price_per_head") not in (None, "") or raw.get("head_count") not in (None, ""):
            raise ValueError("A weight-priced trade cannot contain per-head pricing or counts")
        weights = raw.get("weights_kg")
        if not isinstance(weights, list) or not weights:
            raise ValueError("At least one animal weight is required as a weights_kg array")
        group["weights"] = [decimal_value(value, f"Weight {i}", WEIGHT_QUANT, limit=Decimal("10000000")) for i, value in enumerate(weights, 1)]
        group["price_per_kg"] = decimal_value(raw.get("price_per_kg"), "Price per kg", RATE_QUANT, limit=Decimal("100000000"))
    else:
        if raw.get("price_per_kg") not in (None, "") or raw.get("weights_kg") not in (None, []):
            raise ValueError("A per-head trade cannot contain weights or per-kg pricing")
        group["price_per_head"] = decimal_value(raw.get("price_per_head"), "Price per head", MONEY_QUANT)
        group["head_count"] = positive_int(raw.get("head_count"), "Head count")
        if species == "Goat" and require_details and not group["hair_lengths"]:
            raise ValueError("Hair lengths is required for goats priced per head")
    return group


def template_document():
    return {
        "schema_version": 1, "import_id": str(uuid4()), "trade_type": "sale",
        "date": None, "counterparty": None, "species": "Cattle", "pricing_model": "weight",
        "vat_percent": 15, "reference": None, "notes": None,
        "groups": [{"label": "", "breed": None, "sex": None, "age_class": None,
                    "weights_kg": [], "price_per_kg": None}],
    }


def getlist(source, key):
    if hasattr(source, "getlist"):
        return source.getlist(key)
    value = source.get(key, [])
    return value if isinstance(value, list) else [value]


def split_weights(raw):
    # Blanks are separators in manual entry, but explicit 'null' remains an error.
    return [part for part in re.split(r"[,;\s]+", raw.strip()) if part]


def document_from_form(source):
    """Adapt the grouped editor and the original single-group form to one format."""
    if "group_count" not in source:
        mode = source.get("trade_animal_mode")
        if mode not in ("calf", "lamb", "goat"):
            raise ValueError("Animal mode must be lamb, calf, or goat")
        species = {"calf": "Cattle", "lamb": "Sheep", "goat": "Goat"}[mode]
        raw_vat = source.get("vat_rate")
        vat = decimal_value("15" if raw_vat in (None, "") else raw_vat, "VAT", VAT_QUANT, allow_zero=True, limit=Decimal("101"))
        # Only the legacy form accepts a fractional rate as well as a percentage.
        vat = vat * 100 if vat <= 1 else vat
        group = {key: source.get(key) for key in ("breed", "sex", "age_class", "animal_group_type_id", "hair_lengths")}
        if mode == "goat":
            group.update(head_count=source.get("goat_count"), price_per_head=source.get("price_per_head"))
        else:
            weights = [value for value in getlist(source, "weight_kg") if str(value).strip()]
            weights.extend(split_weights(source.get("weights_csv") or ""))
            group.update(weights_kg=weights, price_per_kg=source.get("price_per_kg"))
        return {"schema_version": 1, "trade_type": source.get("trade_type"), "date": source.get("trade_date"),
                "counterparty": source.get("counterparty"), "species": species, "pricing_model": "head" if mode == "goat" else "weight",
                "vat_percent": str(vat), "notes": source.get("notes"), "reference": source.get("reference"), "groups": [group]}
    count = positive_int(source.get("group_count"), "Group count")
    if count > 1000:
        raise ValueError("A trade can contain at most 1000 groups")
    document = {key: source.get(key) for key in HEADER_FIELDS - {"schema_version", "groups"}}
    document["schema_version"] = 1
    document["groups"] = []
    for index in range(count):
        group = {key: source.get(f"group_{index}_{key}") for key in GROUP_FIELDS - {"weights_kg"}}
        raw_weights = source.get(f"group_{index}_weights_kg") or ""
        group["weights_kg"] = split_weights(raw_weights)
        document["groups"].append(group)
    return document
