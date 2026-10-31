from app.models.orchard import (
    OrchardEmitter,
    OrchardLine,
    OrchardPlant,
    OrchardRow,
    OrchardSpace,
)
from app.modules.orchards.services import space_xy


def snapshot(orchard):
    rows = (
        OrchardRow.query.filter_by(orchard_id=orchard.id)
        .order_by(OrchardRow.created_at, OrchardRow.name, OrchardRow.id)
        .all()
    )
    plants = (
        OrchardPlant.query.filter_by(orchard_id=orchard.id)
        .order_by(OrchardPlant.name, OrchardPlant.id)
        .all()
    )
    lines = (
        OrchardLine.query.filter_by(orchard_id=orchard.id)
        .order_by(OrchardLine.name, OrchardLine.id)
        .all()
    )
    spaces = (
        OrchardSpace.query.filter(OrchardSpace.row_id.in_([row.id for row in rows]))
        .order_by(OrchardSpace.position)
        .all()
    )
    emitters = (
        OrchardEmitter.query.filter(OrchardEmitter.line_id.in_([line.id for line in lines]))
        .order_by(OrchardEmitter.id)
        .all()
    )
    rows_by_id = {row.id: row for row in rows}
    space_data = []
    for space in spaces:
        x_m, y_m = space_xy(rows_by_id[space.row_id], space.position)
        space_data.append({
            "id": space.id,
            "row_id": space.row_id,
            "position": space.position,
            "x_m": x_m,
            "y_m": y_m,
        })
    counts = {
        status: sum(plant.status == status for plant in plants)
        for status in ("unassigned", "planned", "planted")
    }
    return {
        "id": orchard.id,
        "farm_id": orchard.farm_id,
        "name": orchard.name,
        "revision": orchard.revision,
        "summary": {
            **counts,
            "spaces": len(spaces),
            "empty": len(spaces) - counts["planned"] - counts["planted"],
        },
        "rows": [
            {
                "id": row.id,
                "name": row.name,
                "orientation": row.orientation,
                "x_m": row.x_m,
                "y_m": row.y_m,
                "spacing_m": row.spacing_m,
                "label_x_m": row.label_x_m,
                "label_y_m": row.label_y_m,
            }
            for row in rows
        ],
        "spaces": space_data,
        "plants": [
            {
                "id": plant.id,
                "name": plant.name,
                "space_id": plant.space_id,
                "status": plant.status,
                "supplier": plant.supplier,
                "purchased_on": plant.purchased_on.isoformat() if plant.purchased_on else None,
                "planted_on": plant.planted_on.isoformat() if plant.planted_on else None,
            }
            for plant in plants
        ],
        "lines": [
            {"id": line.id, "name": line.name, "vertices": line.vertices, "schematic": line.schematic}
            for line in lines
        ],
        "emitters": [
            {
                "id": emitter.id,
                "line_id": emitter.line_id,
                "kind": emitter.kind,
                "x_m": emitter.x_m,
                "y_m": emitter.y_m,
            }
            for emitter in emitters
        ],
    }
