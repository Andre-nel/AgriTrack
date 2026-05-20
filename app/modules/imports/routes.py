from flask import current_app, flash, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Farm, WaterAsset
from app.services.farm_import_service import FarmImportService
from app.services.water_network_service import WaterNetworkService


def _active_non_imported_water_assets_for_farm(farm_id: str) -> list[WaterAsset]:
    return (
        WaterAsset.query.filter_by(farm_id=farm_id, active=True, import_placemark_name=None)
        .order_by(WaterAsset.asset_type.asc(), WaterAsset.name.asc())
        .all()
    )


def register_legacy_routes(bp) -> None:
    @bp.route("/farms/import", methods=["GET", "POST"])
    def import_farm_page():
        if request.method == "GET":
            return render_template("import_farm.html", default_timezone="UTC")

        uploaded_file = request.files.get("farm_kml")
        file_name = uploaded_file.filename if uploaded_file else ""
        file_bytes = uploaded_file.read() if uploaded_file else b""
        timezone_value = (request.form.get("timezone") or "UTC").strip() or "UTC"

        try:
            result = FarmImportService.import_farm(
                file_name=file_name,
                file_bytes=file_bytes,
                timezone=timezone_value,
                instance_path=current_app.instance_path,
                managed_point_hints=request.form.get("water_managed_ids_json"),
            )
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return redirect(url_for("web.import_farm_page"))

        if result["existing_farm"]:
            retired_note = (
                f", {result['retired_count']} retired" if result.get("retired_count") else ""
            )
            water_note = (
                f"; water assets {result['water_created_count']} added, "
                f"{result['water_updated_count']} updated, "
                f"{result['water_archived_count']} archived"
                if result.get("water_asset_count")
                else ""
            )
            flash(
                (
                    f"Updated farm {result['farm_name']} from import with "
                    f"{result['paddock_count']} paddock(s) "
                    f"({result['created_count']} added, "
                    f"{result['updated_count']} updated{retired_note}){water_note}"
                ),
                "success",
            )
        else:
            water_note = (
                f" and {result['water_asset_count']} water asset(s)"
                if result.get("water_asset_count")
                else ""
            )
            flash(
                f"Imported farm {result['farm_name']} with {result['paddock_count']} paddock(s){water_note}",
                "success",
            )
        non_imported_water_assets = _active_non_imported_water_assets_for_farm(result["farm_id"])
        if non_imported_water_assets:
            flash(
                (
                    f"Review {len(non_imported_water_assets)} active non-imported water asset(s). "
                    "You can keep them or delete selected assets."
                ),
                "info",
            )
            return redirect(url_for("web.review_import_water_assets", farm_id=result["farm_id"]))
        return redirect(url_for("web.farm_detail", farm_id=result["farm_id"]))

    @bp.get("/farms/<farm_id>/import-review/water-assets")
    def review_import_water_assets(farm_id):
        farm = Farm.query.get_or_404(farm_id)
        non_imported_water_assets = _active_non_imported_water_assets_for_farm(str(farm.id))
        if not non_imported_water_assets:
            return redirect(url_for("web.farm_detail", farm_id=farm_id))
        return render_template(
            "import_water_asset_review.html",
            farm=farm,
            water_assets=non_imported_water_assets,
            water_asset_type_labels=WaterNetworkService.ASSET_TYPE_LABELS,
        )

    @bp.post("/farms/<farm_id>/import-review/water-assets")
    def review_import_water_assets_form(farm_id):
        farm = Farm.query.get_or_404(farm_id)
        action = (request.form.get("action") or "keep").strip().lower()
        if action == "keep":
            flash("Kept existing non-imported water assets", "success")
            return redirect(url_for("web.farm_detail", farm_id=farm_id))

        selected_asset_ids = request.form.getlist("asset_id")
        allowed_asset_ids = {
            str(asset.id) for asset in _active_non_imported_water_assets_for_farm(str(farm.id))
        }
        target_asset_ids = [
            asset_id
            for asset_id in (str(raw_id or "").strip() for raw_id in selected_asset_ids)
            if asset_id in allowed_asset_ids
        ]
        if not target_asset_ids:
            flash("Select at least one non-imported water asset to delete", "error")
            return redirect(url_for("web.review_import_water_assets", farm_id=farm_id))

        try:
            deleted_assets = WaterNetworkService.delete_assets(str(farm.id), target_asset_ids)
            db.session.commit()
            flash(f"Deleted {len(deleted_assets)} non-imported water asset(s)", "success")
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return redirect(url_for("web.review_import_water_assets", farm_id=farm_id))
        except IntegrityError:
            db.session.rollback()
            flash("Unable to delete selected non-imported water assets", "error")
            return redirect(url_for("web.review_import_water_assets", farm_id=farm_id))

        remaining_assets = _active_non_imported_water_assets_for_farm(str(farm.id))
        if remaining_assets:
            flash(
                f"{len(remaining_assets)} non-imported water asset(s) still remain for review",
                "info",
            )
            return redirect(url_for("web.review_import_water_assets", farm_id=farm_id))
        return redirect(url_for("web.farm_detail", farm_id=farm_id))

