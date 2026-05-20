def parse_paddock_lines(raw: str) -> list[dict]:
    items = []
    for line in (raw or "").splitlines():
        value = line.strip()
        if not value:
            continue
        parts = [p.strip() for p in value.split(",")]
        name = parts[0]
        area = float(parts[1]) if len(parts) > 1 and parts[1] else 0
        grazeable = float(parts[2]) if len(parts) > 2 and parts[2] else area
        items.append({"name": name, "area_ha": area, "grazeable_area_ha": grazeable})
    return items


def parse_mob_lines(raw: str) -> list[str]:
    return [line.strip() for line in (raw or "").splitlines() if line.strip()]


def parse_placements(raw: str) -> list[tuple[str, str]]:
    placements = []
    for line in (raw or "").splitlines():
        value = line.strip()
        if not value:
            continue
        parts = [p.strip() for p in value.split(",")]
        if len(parts) >= 2 and parts[0] and parts[1]:
            placements.append((parts[0], parts[1]))
    return placements

