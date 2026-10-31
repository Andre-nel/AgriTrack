"""Business operations. Callers own commit/rollback, including revision claims."""

from sqlalchemy import update

from app.extensions import db
from app.models.orchard import Orchard, OrchardEmitter, OrchardLine, OrchardPlant, OrchardRow, OrchardSpace
from app.modules.orchards import validation as v


class RevisionConflict(ValueError):
    pass


def claim_revision(orchard, revision):
    v.integer(revision, "Revision", maximum=2_147_483_646)
    result = db.session.execute(
        update(Orchard)
        .where(Orchard.id == orchard.id, Orchard.revision == revision)
        .values(revision=Orchard.revision + 1)
    )
    if result.rowcount != 1:
        raise RevisionConflict(
            "This orchard changed in another session. Refresh before saving again; "
            "your unsaved edits are still visible."
        )


def owned(model, orchard_id, identity):
    if not isinstance(identity, str):
        raise ValueError("A valid record ID is required.")
    query = model.query
    if model is OrchardSpace:
        query = query.join(OrchardRow, OrchardSpace.row_id == OrchardRow.id).filter(OrchardRow.orchard_id == orchard_id)
    elif model is OrchardEmitter:
        query = query.join(OrchardLine, OrchardEmitter.line_id == OrchardLine.id).filter(OrchardLine.orchard_id == orchard_id)
    else:
        query = query.filter(model.orchard_id == orchard_id)
    record = query.filter(model.id == identity).first()
    if record is None:
        raise ValueError("The selected record does not belong to this orchard.")
    return record


def space_xy(row, position):
    return (
        row.x_m + (position * row.spacing_m if row.orientation == "horizontal" else 0),
        row.y_m + (position * row.spacing_m if row.orientation == "vertical" else 0),
    )


def _validate_extent(row, positions):
    for position in positions:
        x_m, y_m = space_xy(row, position)
        v.number(x_m, "Space x")
        v.number(y_m, "Space y")


def create_row(orchard, payload):
    count = v.integer(payload.get("count"), "Number of spaces")
    row = OrchardRow(orchard_id=orchard.id)
    edit_row(row, payload)
    _validate_extent(row, (0, count - 1))
    db.session.add(row)
    db.session.flush()
    db.session.add_all(OrchardSpace(row_id=row.id, position=i) for i in range(count))
    return row


def edit_row(row, payload):
    row.name = v.label(payload.get("name", row.name))
    row.orientation = v.choice(payload.get("orientation", row.orientation), ("horizontal", "vertical"), "Orientation")
    row.x_m = v.number(payload.get("x_m", row.x_m), "Origin x")
    row.y_m = v.number(payload.get("y_m", row.y_m), "Origin y")
    row.spacing_m = v.number(payload.get("spacing_m", row.spacing_m), "Spacing", positive=True)
    if "label_x_m" in payload or "label_y_m" in payload:
        if "label_x_m" not in payload or "label_y_m" not in payload:
            raise ValueError("Provide both label coordinates, or set both to null to reset the label.")
        if payload["label_x_m"] is None and payload["label_y_m"] is None:
            row.label_x_m = row.label_y_m = None
        else:
            row.label_x_m = v.number(payload["label_x_m"], "Label x")
            row.label_y_m = v.number(payload["label_y_m"], "Label y")
    if row.id:
        _validate_extent(row, (space.position for space in OrchardSpace.query.filter_by(row_id=row.id)))


def resize_row(row, payload):
    end = v.choice(payload.get("end"), ("start", "end"), "Row end")
    action = v.choice(payload.get("action"), ("extend", "trim"), "Resize action")
    count = v.integer(payload.get("count"), "Number of spaces")
    spaces = OrchardSpace.query.filter_by(row_id=row.id).order_by(OrchardSpace.position).all()
    if action == "extend":
        if len(spaces) + count > 1000:
            raise ValueError("A row may have at most 1,000 spaces.")
        if end == "start":
            positions = range(spaces[0].position - count, spaces[0].position)
        else:
            positions = range(spaces[-1].position + 1, spaces[-1].position + count + 1)
        _validate_extent(row, (positions[0], positions[-1]))
        db.session.add_all(OrchardSpace(row_id=row.id, position=i) for i in positions)
        return
    if count >= len(spaces):
        raise ValueError("Keep at least one space, or use Delete row.")
    selected = spaces[:count] if end == "start" else spaces[-count:]
    _require_empty([space.id for space in selected])
    for space in selected:
        db.session.delete(space)


def _require_empty(space_ids):
    if space_ids and OrchardPlant.query.filter(OrchardPlant.space_id.in_(space_ids)).first():
        raise ValueError("These spaces contain planned or planted records. Reassign their plants first.")


def delete_row(row):
    _require_empty([s.id for s in OrchardSpace.query.filter_by(row_id=row.id)])
    db.session.delete(row)


def add_stock(orchard, payload):
    count = v.integer(payload.get("quantity"), "Quantity")
    name = v.label(payload.get("name"), limit=160)
    purchased_on = v.iso_date(payload.get("purchased_on"), "Purchase date")
    supplier = v.label(payload["supplier"], "Supplier", 160) if payload.get("supplier") else None
    db.session.add_all(
        OrchardPlant(
            orchard_id=orchard.id,
            name=name,
            status="unassigned",
            purchased_on=purchased_on,
            supplier=supplier,
        )
        for _ in range(count)
    )


def remove_stock(plant):
    if plant.status not in ("unassigned", "planned"):
        raise ValueError("Planted trees cannot be removed from plant stock.")
    db.session.delete(plant)


def place_plant(plant, payload):
    action = v.choice(payload.get("action"), ("reserve", "cancel", "plant", "correct"), "Placement action")
    if action == "cancel":
        if plant.status != "planned":
            raise ValueError("Only a planned placement can be cancelled.")
        plant.space_id, plant.status = None, "unassigned"
        return
    if action == "plant":
        if plant.status != "planned":
            raise ValueError("Reserve a space before confirming planting.")
        planted_on = v.iso_date(payload.get("planted_on"), "Planting date", required=True)
        if plant.purchased_on and planted_on < plant.purchased_on:
            raise ValueError("Planting date cannot precede the purchase date.")
        plant.planted_on, plant.status = planted_on, "planted"
        return
    if action == "reserve" and plant.status not in ("unassigned", "planned"):
        raise ValueError("Use location correction for an existing planted record.")
    if action == "correct" and plant.status != "planted":
        raise ValueError("Location correction is only for planted records.")
    space = owned(OrchardSpace, plant.orchard_id, payload.get("space_id"))
    occupant = OrchardPlant.query.filter_by(space_id=space.id).first()
    if occupant and occupant.id != plant.id:
        raise ValueError("That space already contains a planned or planted record.")
    plant.space_id = space.id
    if action == "reserve":
        plant.status = "planned"


def save_line(orchard, payload, line=None):
    if line is None:
        line = OrchardLine(orchard_id=orchard.id, schematic=False)
        db.session.add(line)
    line.name = v.label(payload.get("name", line.name))
    if "vertices" in payload or line.vertices is None:
        points = v.vertices(payload.get("vertices"))
        if line.id:
            for emitter in OrchardEmitter.query.filter_by(line_id=line.id):
                emitter.x_m, emitter.y_m = v.project([emitter.x_m, emitter.y_m], points)
        line.vertices = points
    # 'schematic' remains true for seed lines until the user explicitly verifies them.
    if "schematic" in payload:
        if type(payload["schematic"]) is not bool:
            raise ValueError("Schematic must be true or false.")
        line.schematic = payload["schematic"]
    return line


def save_emitter(line, payload, emitter=None):
    if emitter is None:
        emitter = OrchardEmitter(line_id=line.id)
        db.session.add(emitter)
    emitter.kind = v.choice(
        payload.get("kind", emitter.kind or "dripper"),
        ("dripper", "sprinkler"),
        "Emitter kind",
    )
    point = [
        v.number(payload.get("x_m", emitter.x_m), "Emitter x"),
        v.number(payload.get("y_m", emitter.y_m), "Emitter y"),
    ]
    emitter.x_m, emitter.y_m = v.project(point, line.vertices)
    return emitter
