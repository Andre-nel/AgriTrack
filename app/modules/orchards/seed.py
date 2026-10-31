import json
from pathlib import Path

import click
from flask.cli import with_appcontext
from sqlalchemy import func

from app.extensions import db
from app.models import Farm
from app.models.orchard import Orchard, OrchardLine, OrchardPlant, OrchardSpace
from app.modules.orchards.services import add_stock, create_row, space_xy

FIXTURE = Path(__file__).parent / "fixtures" / "rietfontein_v1.json"


def import_rietfontein(farm):
    """A named observation import, not demo data or a migration side effect."""
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    existing = Orchard.query.filter_by(farm_id=farm.id, seed_key=data["seed_key"]).first()
    if existing:
        return existing, False
    orchard = Orchard(farm_id=farm.id, name=data["name"], seed_key=data["seed_key"])
    db.session.add(orchard)
    db.session.flush()
    for index, names in enumerate(data["rows"]):
        row = create_row(orchard, {
            "name": f"Row {index + 1}", "orientation": "horizontal", "count": len(names),
            "x_m": (data["start_columns"][index] - 1) * data["tree_spacing_m"],
            "y_m": index * data["row_spacing_m"], "spacing_m": data["tree_spacing_m"],
        })
        spaces = OrchardSpace.query.filter_by(row_id=row.id).order_by(OrchardSpace.position).all()
        for space, name in zip(spaces, names):
            if name is not None:
                db.session.add(OrchardPlant(orchard_id=orchard.id, space_id=space.id, name=name, status="planted"))
        db.session.add(OrchardLine(orchard_id=orchard.id, name=f"{row.name} dripper", schematic=True, vertices=[list(space_xy(row, 0)), list(space_xy(row, len(names) - 1))]))
    purchase = data["purchase"]
    for item in purchase["items"]:
        add_stock(orchard, {**item, "purchased_on": purchase["purchased_on"], "supplier": purchase["supplier"]})
    db.session.flush()
    return orchard, True


@click.command("import-orchard")
@click.option("--farm", "farm_name", default="Rietfontein", show_default=True, help="Exact farm name or ID receiving the supplied orchard observation.")
@with_appcontext
def import_orchard_command(farm_name):
    farm = Farm.query.filter((Farm.id == farm_name) | (func.lower(Farm.name) == farm_name.lower())).first()
    if farm is None:
        raise click.ClickException(f"Farm not found: {farm_name}")
    try:
        orchard, created = import_rietfontein(farm)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    click.echo(f"{'Imported' if created else 'Already imported; preserved existing edits'}: {orchard.name} ({orchard.id})")
