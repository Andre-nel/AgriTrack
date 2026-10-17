"""Decimal calculations for normalized groups, shared by preview and saved trades."""

from decimal import Decimal, ROUND_HALF_UP

from app.services.livestock_trade_payload import MONEY_QUANT, RATE_QUANT, WEIGHT_QUANT


def rounded(value, quant=MONEY_QUANT):
    return value.quantize(quant, rounding=ROUND_HALF_UP)


def group_statistics(group, pricing_model, vat_rate):
    if pricing_model == "weight":
        count = len(group["weights"])
        total_weight = sum(group["weights"], Decimal("0.000"))
        unit_price = group["price_per_kg"]
        base_total = rounded(total_weight * unit_price)
    else:
        count = group["head_count"]
        total_weight = None
        unit_price = group["price_per_head"]
        base_total = rounded(Decimal(count) * unit_price)
    vat_amount = rounded(base_total * vat_rate)
    return {
        "count": count, "total_weight": total_weight,
        "average_weight": rounded(total_weight / count, WEIGHT_QUANT) if total_weight is not None else None,
        "unit_price": unit_price,
        "gross_unit_price": rounded(unit_price * (1 + vat_rate), RATE_QUANT if pricing_model == "weight" else MONEY_QUANT),
        "base_total": base_total, "total_money": base_total,
        "vat_amount": vat_amount, "gross_total": base_total + vat_amount,
        "average_price_per_animal": rounded(base_total / count),
    }


def combine_statistics(rows, pricing_model):
    count = sum(row["count"] for row in rows)
    total_weight = sum((row["total_weight"] for row in rows), Decimal("0.000")) if pricing_model == "weight" else None
    base = sum((row["base_total"] for row in rows), Decimal("0.00"))
    vat = sum((row["vat_amount"] for row in rows), Decimal("0.00"))
    denominator = total_weight if pricing_model == "weight" else count
    return {
        "count": count, "total_weight": total_weight,
        "average_weight": rounded(total_weight / count, WEIGHT_QUANT) if count and total_weight is not None else None,
        "unit_price": rounded(base / denominator, RATE_QUANT if pricing_model == "weight" else MONEY_QUANT) if denominator else None,
        "gross_unit_price": rounded((base + vat) / denominator, RATE_QUANT if pricing_model == "weight" else MONEY_QUANT) if denominator else None,
        "base_total": base, "total_money": base, "vat_amount": vat, "gross_total": base + vat,
        "average_price_per_animal": rounded(base / count) if count else None,
    }


def payload_statistics(payload):
    groups = [group_statistics(group, payload["pricing_model"], payload["vat_rate"]) for group in payload["groups"]]
    total = combine_statistics(groups, payload["pricing_model"])
    if total["gross_total"] <= 0 or total["gross_total"] >= Decimal("10000000000"):
        raise ValueError("Total including VAT must be greater than zero and less than R10,000,000,000")
    sexes = {}
    for group, stats in zip(payload["groups"], groups):
        sexes.setdefault(group["sex"] or "unresolved", []).append(stats)
    return {
        "groups": groups,
        "stats": total,
        "sex_stats": {sex: combine_statistics(rows, payload["pricing_model"]) for sex, rows in sexes.items()},
    }
