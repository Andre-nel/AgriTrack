from decimal import Decimal


def parse_move_allocations(
    paddock_ids: list[str],
    allocation_pcts: list[str],
    valid_paddock_ids: set[str],
) -> list[dict[str, str]]:
    allocations = []
    used_paddocks = set()
    total_pct = Decimal("0")

    for paddock_id_raw, pct_raw in zip(paddock_ids, allocation_pcts):
        paddock_id = (paddock_id_raw or "").strip()
        pct_text = (pct_raw or "").strip()
        if not paddock_id and not pct_text:
            continue
        if not paddock_id:
            raise ValueError("Each allocation row requires a paddock")
        if paddock_id not in valid_paddock_ids:
            raise ValueError("Selected paddock is invalid for the chosen destination farm")
        if paddock_id in used_paddocks:
            raise ValueError("Duplicate paddock rows are not allowed")
        if not pct_text:
            raise ValueError("Each allocation row requires a percentage")

        pct = Decimal(pct_text)
        if pct <= 0:
            raise ValueError("Allocation percentages must be greater than 0")
        if pct > 100:
            raise ValueError("Allocation percentages cannot exceed 100")

        used_paddocks.add(paddock_id)
        total_pct += pct
        allocations.append(
            {
                "paddock_id": paddock_id,
                "allocation_fraction": str(pct / Decimal("100")),
            }
        )

    if not allocations:
        raise ValueError("At least one paddock allocation is required")
    if total_pct != Decimal("100"):
        raise ValueError(f"Allocation percentages must add up to 100, got: {total_pct}")
    return allocations


def parse_count_move_allocations(
    paddock_ids: list[str],
    group_counts_by_id: dict[str, list[str]],
    valid_paddock_ids: set[str],
) -> list[dict]:
    allocations = []
    used_paddocks = set()

    for index, paddock_id_raw in enumerate(paddock_ids):
        paddock_id = (paddock_id_raw or "").strip()
        group_counts = []
        has_any_count_text = False

        for group_id, values in group_counts_by_id.items():
            raw_value = values[index] if index < len(values) else ""
            qty_text = (raw_value or "").strip()
            if qty_text:
                has_any_count_text = True
            try:
                quantity = int(qty_text) if qty_text else 0
            except ValueError:
                raise ValueError("Count allocations must be whole numbers")
            if quantity < 0:
                raise ValueError("Count allocations cannot be negative")
            group_counts.append({"animal_group_type_id": group_id, "head_count": quantity})

        if not paddock_id and not has_any_count_text:
            continue
        if not paddock_id:
            raise ValueError("Each count allocation row requires a paddock")
        if paddock_id not in valid_paddock_ids:
            raise ValueError("Selected paddock is invalid for the chosen destination farm")
        if paddock_id in used_paddocks:
            raise ValueError("Duplicate paddock rows are not allowed")
        if not any(item["head_count"] > 0 for item in group_counts):
            raise ValueError("Each count allocation row must assign at least one animal")

        used_paddocks.add(paddock_id)
        allocations.append({"paddock_id": paddock_id, "group_counts": group_counts})

    if not allocations:
        raise ValueError("At least one count allocation row is required")
    return allocations


def parse_transfer_rows(group_ids: list[str], quantities: list[str]) -> list[dict[str, int]]:
    transfer_totals: dict[str, int] = {}
    for group_id_raw, qty_raw in zip(group_ids, quantities):
        group_id = (group_id_raw or "").strip()
        qty_text = (qty_raw or "").strip()

        if not group_id and not qty_text:
            continue
        if not group_id or not qty_text:
            raise ValueError("Each transfer row requires a group and quantity")

        try:
            quantity = int(qty_text)
        except ValueError:
            raise ValueError("Transfer quantities must be whole numbers")
        if quantity <= 0:
            raise ValueError("Transfer quantities must be greater than 0")

        transfer_totals[group_id] = transfer_totals.get(group_id, 0) + quantity

    if not transfer_totals:
        raise ValueError("Add at least one transfer row")

    return [
        {"animal_group_type_id": group_id, "quantity": quantity}
        for group_id, quantity in transfer_totals.items()
    ]


def parse_split_rows(
    names: list[str],
    group_ids: list[str],
    quantities: list[str],
) -> list[dict]:
    split_map: dict[str, dict[str, int]] = {}

    for name_raw, group_id_raw, qty_raw in zip(names, group_ids, quantities):
        name = (name_raw or "").strip()
        group_id = (group_id_raw or "").strip()
        qty_text = (qty_raw or "").strip()

        if not name and not group_id and not qty_text:
            continue
        if not name or not group_id or not qty_text:
            raise ValueError("Each split row requires a mob name, group, and quantity")

        try:
            qty = int(qty_text)
        except ValueError:
            raise ValueError("Split quantities must be whole numbers")
        if qty <= 0:
            raise ValueError("Split quantities must be greater than 0")

        split_map.setdefault(name, {})
        split_map[name][group_id] = split_map[name].get(group_id, 0) + qty

    if not split_map:
        raise ValueError("Add at least one split allocation row")

    return [
        {
            "name": name,
            "groups": [
                {"animal_group_type_id": group_id, "quantity": quantity}
                for group_id, quantity in group_totals.items()
            ],
        }
        for name, group_totals in split_map.items()
    ]
