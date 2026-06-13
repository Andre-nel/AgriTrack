from werkzeug.datastructures import MultiDict


SECTION_FORM_FIELDS = (
    "name",
    "condition",
    "height_profile",
    "construction_type",
    "post_type",
    "dropper_type",
    "wire_type",
    "mesh_type",
    "electric_wire_type",
    "notes",
    "tags",
    "holds_cattle",
    "holds_sheep",
    "holds_goats",
    "excludes_jackal",
    "excludes_predators",
)


def section_payload_from_form(form: MultiDict) -> dict:
    payload = {field: form.get(field) for field in SECTION_FORM_FIELDS if field in form}
    payload["electric_wire"] = "electric_wire" in form
    return payload


def material_rows_from_form(form: MultiDict) -> list[dict]:
    actions = form.getlist("material_action")
    material_types = form.getlist("material_type")
    details = form.getlist("material_detail")
    quantities = form.getlist("material_quantity")
    units = form.getlist("material_unit")
    notes = form.getlist("material_notes")
    row_count = max(
        len(actions),
        len(material_types),
        len(details),
        len(quantities),
        len(units),
        len(notes),
    )
    rows = []
    for index in range(row_count):
        rows.append(
            {
                "action": actions[index] if index < len(actions) else "",
                "material_type": material_types[index] if index < len(material_types) else "",
                "material_detail": details[index] if index < len(details) else "",
                "quantity": quantities[index] if index < len(quantities) else "",
                "unit": units[index] if index < len(units) else "",
                "notes": notes[index] if index < len(notes) else "",
            }
        )
    return rows
