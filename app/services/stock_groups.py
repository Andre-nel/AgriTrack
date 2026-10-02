"""Read animal-type totals and divide whole animals without losing a head."""

from decimal import Decimal, ROUND_DOWN


def apportion_counts(total_count: int, weighted_items: list[tuple]) -> dict[str, int]:
    """Use largest remainders; stable string keys break otherwise equal ties."""
    total = int(total_count or 0)
    items = [(str(key), max(Decimal(str(weight)), Decimal(0))) for key, weight in weighted_items]
    if total <= 0 or not items:
        return {key: 0 for key, _weight in items}
    if not any(weight > 0 for _key, weight in items):
        items = [(key, Decimal(1)) for key, _weight in items]
    denominator = sum(weight for _key, weight in items)
    counts = {}
    remainders = []
    for key, weight in items:
        share = Decimal(total) * weight / denominator
        count = int(share.to_integral_value(rounding=ROUND_DOWN))
        counts[key] = count
        if weight > 0:
            remainders.append((share - count, key))
    remaining = total - sum(counts.values())
    for _remainder, key in sorted(remainders, key=lambda row: (-row[0], row[1]))[:remaining]:
        counts[key] += 1
    return counts


def summarize_balances(balances) -> list[dict]:
    """Cohort state never changes the identity of a general stock count."""
    by_group = {}
    for balance in balances:
        count = int(balance.head_count or 0)
        if count <= 0:
            continue
        group_id = str(balance.animal_group_type_id)
        row = by_group.setdefault(
            group_id,
            {"animal_group_type_id": group_id, "group": balance.animal_group_type, "head_count": 0},
        )
        row["head_count"] += count
    return sorted(
        by_group.values(),
        key=lambda row: (
            row["group"].species.lower(), row["group"].breed.lower(),
            row["group"].sex.lower(), row["group"].age_class.lower(), row["animal_group_type_id"],
        ),
    )
