from urllib.parse import urlencode

from flask import flash, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Farm, Paddock, WaterAsset, WaterConnection
from app.modules.water.forms import (
    normalize_water_asset_type_filters,
    water_asset_form_payload,
    water_asset_mass_update_form_payload,
    water_connection_form_payload,
    water_mass_update_field_specs,
)
from app.services.water_network_service import WaterNetworkService


def _farm_water_workspace_redirect_response(farm_id: str, *, open_asset_id: str | None = None):
    redirect_values = {"farm_id": farm_id}
    if open_asset_id:
        redirect_values["open_asset_id"] = open_asset_id
    return redirect(url_for("web.farm_water_workspace", **redirect_values))


def _farm_water_mass_update_redirect_response(
    farm_id: str,
    *,
    selected_asset_types: list[str] | None = None,
):
    target_url = url_for("web.farm_water_mass_update", farm_id=farm_id)
    if selected_asset_types:
        target_url += "?" + urlencode({"asset_type": selected_asset_types}, doseq=True)
    return redirect(target_url)


def register_legacy_routes(bp) -> None:
    @bp.get("/farms/<farm_id>/water")
    def farm_water_workspace(farm_id):
        farm = Farm.query.get_or_404(farm_id)
        paddocks = (
            Paddock.query.filter_by(farm_id=farm.id, status="active")
            .order_by(Paddock.name.asc())
            .all()
        )
        assets = WaterNetworkService.assets_for_farm(str(farm.id))
        connections = WaterNetworkService.connections_for_farm(str(farm.id))
        water_summary = WaterNetworkService.farm_summary(str(farm.id))
        active_assets = [asset for asset in assets if asset.active]
        pump_assets = [
            asset for asset in active_assets if asset.asset_type in WaterNetworkService.PUMP_ASSET_TYPES
        ]
        map_filters_applied = (request.args.get("map_filters_applied") or "").strip() == "1"
        selected_map_asset_types = normalize_water_asset_type_filters(
            request.args.getlist("asset_type"),
            filters_applied=map_filters_applied,
        )
        water_asset_editor_config = {
            "assetTypeLabels": WaterNetworkService.ASSET_TYPE_LABELS,
            "assetRecords": [
                {
                    "id": str(asset.id),
                    "name": asset.name,
                    "assetType": asset.asset_type,
                    "assetTypeLabel": WaterNetworkService.ASSET_TYPE_LABELS.get(
                        asset.asset_type, asset.asset_type
                    ),
                    "active": asset.active,
                }
                for asset in assets
            ],
            "connectionRecords": [
                WaterNetworkService.serialize_connection(connection) for connection in connections
            ],
            "flowTypes": list(WaterNetworkService.FLOW_TYPES),
            "flowTypeLabels": WaterNetworkService.FLOW_TYPE_LABELS,
            "statusOptionsByType": {
                asset_type: sorted(options)
                for asset_type, options in WaterNetworkService.STATUS_OPTIONS_BY_TYPE.items()
            },
            "materialOptionsByType": {
                asset_type: sorted(options)
                for asset_type, options in WaterNetworkService.MATERIAL_OPTIONS_BY_TYPE.items()
            },
            "waterLevelOptions": list(WaterNetworkService.WATER_LEVEL_OPTIONS),
            "waterLevelTypes": sorted(WaterNetworkService.WATER_LEVEL_TYPES),
            "capacityTypes": sorted(WaterNetworkService.CAPACITY_TYPES),
            "windmillSizeOptions": list(WaterNetworkService.WINDMILL_SIZE_OPTIONS),
            "weirSizeOptions": list(WaterNetworkService.WEIR_SIZE_OPTIONS),
            "troughSizeOptions": list(WaterNetworkService.TROUGH_SIZE_OPTIONS),
            "solarFieldTypes": ["solarpump"],
            "sourceSystemTypes": ["solarpump"],
            "weirFieldTypes": ["weir"],
            "troughFieldTypes": ["trough"],
            "servedPaddockTypes": sorted(WaterNetworkService.SERVED_PADDOCK_TYPES),
            "locationBoundServedPaddockTypes": sorted(
                WaterNetworkService.LOCATION_BOUND_SERVED_PADDOCK_TYPES
            ),
            "pumpAssetTypes": sorted(WaterNetworkService.PUMP_ASSET_TYPES),
            "gravityTroughSourceTypes": sorted(WaterNetworkService.GRAVITY_TROUGH_SOURCE_TYPES),
            "transferSourceTypes": sorted(WaterNetworkService.TRANSFER_SOURCE_TYPES),
            "transferDestinationTypes": sorted(WaterNetworkService.TRANSFER_DESTINATION_TYPES),
            "defaultTroughConnection": {
                "pipeMaterial": WaterNetworkService.DEFAULT_TROUGH_CONNECTION_PIPE_MATERIAL,
                "pipeDiameterSpec": WaterNetworkService.DEFAULT_TROUGH_CONNECTION_PIPE_DIAMETER_SPEC,
                "pipeClassSpec": WaterNetworkService.DEFAULT_TROUGH_CONNECTION_PIPE_CLASS_SPEC,
            },
        }

        return render_template(
            "farm_water.html",
            farm=farm,
            paddocks=paddocks,
            assets=assets,
            active_assets=active_assets,
            connections=connections,
            water_summary=water_summary,
            pump_assets=pump_assets,
            water_asset_types=WaterNetworkService.ASSET_TYPES,
            water_asset_type_labels=WaterNetworkService.ASSET_TYPE_LABELS,
            water_flow_types=WaterNetworkService.FLOW_TYPES,
            water_flow_type_labels=WaterNetworkService.FLOW_TYPE_LABELS,
            status_options_by_type=WaterNetworkService.STATUS_OPTIONS_BY_TYPE,
            all_status_options=sorted(
                {
                    option
                    for options in WaterNetworkService.STATUS_OPTIONS_BY_TYPE.values()
                    for option in options
                }
            ),
            water_level_options=WaterNetworkService.WATER_LEVEL_OPTIONS,
            weir_size_options=WaterNetworkService.WEIR_SIZE_OPTIONS,
            trough_size_options=WaterNetworkService.TROUGH_SIZE_OPTIONS,
            material_options_by_type=WaterNetworkService.MATERIAL_OPTIONS_BY_TYPE,
            all_material_options=sorted(
                {
                    option
                    for options in WaterNetworkService.MATERIAL_OPTIONS_BY_TYPE.values()
                    for option in options
                }
            ),
            map_filters_applied=map_filters_applied,
            selected_map_asset_types=selected_map_asset_types,
            water_asset_editor_config=water_asset_editor_config,
        )

    @bp.get("/farms/<farm_id>/water/mass-update")
    def farm_water_mass_update(farm_id):
        farm = Farm.query.get_or_404(farm_id)
        paddocks = (
            Paddock.query.filter_by(farm_id=farm.id, status="active")
            .order_by(Paddock.name.asc())
            .all()
        )
        mass_update_paddocks = [{"id": str(paddock.id), "name": paddock.name} for paddock in paddocks]
        selected_asset_types = normalize_water_asset_type_filters(
            request.args.getlist("asset_type"),
            filters_applied=True,
        )
        selected_asset_type_set = set(selected_asset_types)
        assets = [
            WaterNetworkService.serialize_asset(asset)
            for asset in WaterNetworkService.assets_for_farm(str(farm.id))
            if asset.asset_type in selected_asset_type_set
        ]
        mass_update_fields = water_mass_update_field_specs(selected_asset_types)

        return render_template(
            "farm_water_mass_update.html",
            farm=farm,
            water_asset_types=WaterNetworkService.ASSET_TYPES,
            water_asset_type_labels=WaterNetworkService.ASSET_TYPE_LABELS,
            selected_mass_update_asset_types=selected_asset_types,
            selected_mass_update_assets=assets,
            mass_update_fields=mass_update_fields,
            mass_update_paddocks=mass_update_paddocks,
            has_multiple_mass_update_types=len(selected_asset_types) > 1,
        )

    @bp.post("/farms/<farm_id>/water/assets")
    def create_water_asset_form(farm_id):
        Farm.query.get_or_404(farm_id)
        try:
            WaterNetworkService.create_asset(water_asset_form_payload(request.form, farm_id=farm_id))
            db.session.commit()
            flash("Water asset created", "success")
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
        except IntegrityError:
            db.session.rollback()
            flash("Unable to save water asset", "error")
        return redirect(url_for("web.farm_water_workspace", farm_id=farm_id))

    @bp.post("/farms/<farm_id>/water/assets/<asset_id>")
    def update_water_asset_form(farm_id, asset_id):
        Farm.query.get_or_404(farm_id)
        asset = WaterAsset.query.filter_by(id=asset_id, farm_id=farm_id).first_or_404()
        try:
            WaterNetworkService.update_asset(
                asset, water_asset_form_payload(request.form, farm_id=farm_id)
            )
            db.session.commit()
            flash("Water asset updated", "success")
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return redirect(
                url_for("web.farm_water_workspace", farm_id=farm_id, open_asset_id=asset_id)
            )
        except IntegrityError:
            db.session.rollback()
            flash("Unable to update water asset", "error")
            return redirect(
                url_for("web.farm_water_workspace", farm_id=farm_id, open_asset_id=asset_id)
            )
        return redirect(url_for("web.farm_water_workspace", farm_id=farm_id))

    @bp.post("/farms/<farm_id>/water/mass-update")
    def update_water_assets_mass_form(farm_id):
        Farm.query.get_or_404(farm_id)
        selected_asset_types = normalize_water_asset_type_filters(
            request.form.getlist("asset_type"),
            filters_applied=True,
        )
        selected_asset_type_set = set(selected_asset_types)
        raw_asset_ids = [
            str(asset_id or "").strip() for asset_id in request.form.getlist("asset_id")
        ]
        asset_ids = []
        seen_asset_ids = set()
        for asset_id in raw_asset_ids:
            if not asset_id or asset_id in seen_asset_ids:
                continue
            seen_asset_ids.add(asset_id)
            asset_ids.append(asset_id)

        if not selected_asset_types:
            flash("Select at least one water asset type to mass update", "error")
            return _farm_water_mass_update_redirect_response(
                farm_id, selected_asset_types=selected_asset_types
            )
        if not asset_ids:
            flash("No water assets were selected for mass update", "error")
            return _farm_water_mass_update_redirect_response(
                farm_id, selected_asset_types=selected_asset_types
            )

        assets = WaterAsset.query.filter(
            WaterAsset.farm_id == farm_id, WaterAsset.id.in_(asset_ids)
        ).all()
        assets_by_id = {str(asset.id): asset for asset in assets}

        try:
            for asset_id in asset_ids:
                asset = assets_by_id.get(asset_id)
                if asset is None:
                    raise ValueError("One or more selected water assets are invalid")
                if asset.asset_type not in selected_asset_type_set:
                    raise ValueError(
                        "One or more selected water assets do not match the chosen asset types"
                    )
                WaterNetworkService.update_asset(
                    asset,
                    water_asset_mass_update_form_payload(
                        request.form,
                        farm_id=farm_id,
                        asset_id=asset_id,
                    ),
                )
            db.session.commit()
            flash(f"Updated {len(asset_ids)} water asset(s)", "success")
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
        except IntegrityError:
            db.session.rollback()
            flash("Unable to mass update water assets", "error")
        return _farm_water_mass_update_redirect_response(
            farm_id, selected_asset_types=selected_asset_types
        )

    @bp.post("/farms/<farm_id>/water/connections")
    def create_water_connection_form(farm_id):
        Farm.query.get_or_404(farm_id)
        open_asset_id = (request.form.get("open_asset_id") or "").strip() or None
        try:
            WaterNetworkService.create_connection(
                water_connection_form_payload(request.form, farm_id=farm_id)
            )
            db.session.commit()
            flash("Water connection created", "success")
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
        except IntegrityError:
            db.session.rollback()
            flash("Unable to save water connection", "error")
        return _farm_water_workspace_redirect_response(farm_id, open_asset_id=open_asset_id)

    @bp.post("/farms/<farm_id>/water/connections/<connection_id>")
    def update_water_connection_form(farm_id, connection_id):
        Farm.query.get_or_404(farm_id)
        connection = WaterConnection.query.filter_by(id=connection_id, farm_id=farm_id).first_or_404()
        open_asset_id = (request.form.get("open_asset_id") or "").strip() or None
        try:
            WaterNetworkService.update_connection(
                connection,
                water_connection_form_payload(request.form, farm_id=farm_id),
            )
            db.session.commit()
            flash("Water connection updated", "success")
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
        except IntegrityError:
            db.session.rollback()
            flash("Unable to update water connection", "error")
        return _farm_water_workspace_redirect_response(farm_id, open_asset_id=open_asset_id)

    @bp.post("/farms/<farm_id>/water/connections/<connection_id>/delete")
    def delete_water_connection_form(farm_id, connection_id):
        Farm.query.get_or_404(farm_id)
        connection = WaterConnection.query.filter_by(id=connection_id, farm_id=farm_id).first_or_404()
        open_asset_id = (request.form.get("open_asset_id") or "").strip() or None
        db.session.delete(connection)
        db.session.commit()
        flash("Water connection deleted", "success")
        return _farm_water_workspace_redirect_response(farm_id, open_asset_id=open_asset_id)

