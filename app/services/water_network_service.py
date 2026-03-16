from decimal import Decimal, InvalidOperation

from app.extensions import db
from app.models import Paddock, WaterAsset, WaterAssetServedPaddock, WaterConnection
from app.models.water import WATER_ASSET_TYPES, WATER_CONNECTION_FLOW_TYPES
from app.services.paddock_service import PaddockService


class WaterNetworkService:
    MAX_NAME_LENGTH = 120

    ASSET_TYPES = WATER_ASSET_TYPES
    FLOW_TYPES = WATER_CONNECTION_FLOW_TYPES
    ASSET_TYPE_LABELS = {
        "borehole": "Borehole",
        "pit": "Pit",
        "windmill": "Windmill",
        "solarpump": "Solar Pump",
        "cement_dam": "Cement Dam",
        "tank": "Tank",
        "ground_dam": "Ground Dam",
        "weir": "Wear",
        "trough": "Trough",
    }
    FLOW_TYPE_LABELS = {"pumped": "Pumped", "gravity": "Gravity"}
    PUMP_ASSET_TYPES = {"windmill", "solarpump"}
    STORAGE_ASSET_TYPES = {"cement_dam", "tank", "ground_dam"}
    WATER_LEVEL_TYPES = {"pit", "cement_dam", "tank", "ground_dam", "weir", "trough"}
    WATER_LEVEL_OPTIONS = ("empty", "low", "half", "high", "full")
    TROUGH_SIZE_OPTIONS = ("small", "medium", "large")
    MATERIAL_OPTIONS_BY_TYPE = {
        "pit": {"earth"},
        "cement_dam": {"concrete"},
        "tank": {"concrete", "plastic", "steel"},
        "ground_dam": {"earth"},
        "trough": {"concrete", "plastic", "steel"},
    }
    STATUS_OPTIONS_BY_TYPE = {
        "borehole": {"operational", "limited", "dry"},
        "pit": {"operational", "limited", "dry"},
        "windmill": {"operational", "service_due", "down"},
        "solarpump": {"operational", "service_due", "down"},
        "cement_dam": {"operational", "leaking", "damaged"},
        "tank": {"operational", "leaking", "damaged"},
        "ground_dam": {"operational", "silted", "damaged"},
        "weir": {"operational", "silted", "damaged"},
        "trough": {"operational", "leaking", "damaged"},
    }
    GRAVITY_SOURCE_TYPES = {"cement_dam", "tank"}
    GRAVITY_DESTINATION_TYPES = {"trough"}
    PUMPED_SOURCE_TYPES = {"borehole", "pit", "cement_dam", "tank", "ground_dam", "weir"}
    PUMPED_DESTINATION_TYPES = {"cement_dam", "tank"}

    @staticmethod
    def normalize_name(value: str | None) -> str:
        return PaddockService.normalize_name(value)

    @classmethod
    def normalize_choice(cls, value: str | None) -> str:
        return cls.normalize_name(value).replace("-", "_").replace(" ", "_").lower()

    @classmethod
    def validate_name(cls, value: str | None) -> str:
        name = cls.normalize_name(value)
        if not name:
            raise ValueError("Water asset name is required")
        if len(name) > cls.MAX_NAME_LENGTH:
            raise ValueError(f"Water asset name must be {cls.MAX_NAME_LENGTH} characters or fewer")
        if any(ord(char) < 32 for char in name):
            raise ValueError("Water asset name contains invalid characters")
        return name

    @staticmethod
    def _to_float(value):
        if value is None:
            return None
        return float(value)

    @staticmethod
    def _current_value(payload: dict, field: str, current):
        return payload[field] if field in payload else current

    @staticmethod
    def _coerce_optional_decimal(value, field_name: str) -> Decimal | None:
        if value in (None, ""):
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"{field_name} must be a valid number") from exc

    @staticmethod
    def _coerce_optional_integer(value, field_name: str) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(str(value))
        except ValueError as exc:
            raise ValueError(f"{field_name} must be a whole number") from exc

    @staticmethod
    def _coerce_boolean(value, field_name: str) -> bool:
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"{field_name} must be true or false")

    @staticmethod
    def _coerce_optional_text(value) -> str | None:
        text = " ".join(str(value or "").strip().split())
        return text or None

    @classmethod
    def _coerce_choice(cls, value, field_name: str, *, allowed: set[str] | tuple[str, ...]) -> str | None:
        normalized = cls.normalize_choice(value)
        if not normalized:
            return None
        if normalized not in allowed:
            allowed_text = ", ".join(sorted(allowed))
            raise ValueError(f"{field_name} must be one of: {allowed_text}")
        return normalized

    @staticmethod
    def _coerce_id_list(value) -> list[str]:
        if value in (None, "", []):
            return []
        if isinstance(value, (list, tuple, set)):
            values = value
        else:
            values = [item.strip() for item in str(value).split(",")]
        deduped = []
        seen = set()
        for item in values:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            deduped.append(text)
        return deduped

    @classmethod
    def _paddock_map_for_ids(cls, farm_id: str, paddock_ids: list[str]) -> dict[str, Paddock]:
        if not paddock_ids:
            return {}
        with db.session.no_autoflush:
            rows = Paddock.query.filter(Paddock.farm_id == farm_id, Paddock.id.in_(paddock_ids)).all()
        return {str(row.id): row for row in rows}

    @classmethod
    def validate_asset_payload(
        cls,
        payload: dict,
        *,
        asset: WaterAsset | None = None,
        imported: bool = False,
    ) -> dict:
        farm_id = cls._current_value(payload, "farm_id", asset.farm_id if asset else None)
        if not farm_id:
            raise ValueError("farm_id is required")

        name = cls.validate_name(cls._current_value(payload, "name", asset.name if asset else None))
        asset_type = cls._coerce_choice(
            cls._current_value(payload, "asset_type", asset.asset_type if asset else None),
            "asset_type",
            allowed=set(cls.ASSET_TYPES),
        )
        if not asset_type:
            raise ValueError("asset_type is required")

        active_value = cls._current_value(payload, "active", asset.active if asset else True)
        active = cls._coerce_boolean(active_value, "active")

        if "needs_review" in payload:
            needs_review = cls._coerce_boolean(payload["needs_review"], "needs_review")
        elif asset is not None:
            needs_review = asset.needs_review
        else:
            needs_review = bool(imported)

        location_paddock_id = cls._current_value(
            payload,
            "location_paddock_id",
            asset.location_paddock_id if asset else None,
        )
        location_paddock_id = str(location_paddock_id).strip() if location_paddock_id else None

        latitude = cls._coerce_optional_decimal(
            cls._current_value(payload, "latitude", asset.latitude if asset else None),
            "latitude",
        )
        longitude = cls._coerce_optional_decimal(
            cls._current_value(payload, "longitude", asset.longitude if asset else None),
            "longitude",
        )
        altitude_m = cls._coerce_optional_decimal(
            cls._current_value(payload, "altitude_m", asset.altitude_m if asset else None),
            "altitude_m",
        )
        capacity_m3 = cls._coerce_optional_decimal(
            cls._current_value(payload, "capacity_m3", asset.capacity_m3 if asset else None),
            "capacity_m3",
        )
        solar_kw = cls._coerce_optional_decimal(
            cls._current_value(payload, "solar_kw", asset.solar_kw if asset else None),
            "solar_kw",
        )
        solar_head_m = cls._coerce_optional_decimal(
            cls._current_value(payload, "solar_head_m", asset.solar_head_m if asset else None),
            "solar_head_m",
        )
        windmill_size_ft = cls._coerce_optional_integer(
            cls._current_value(payload, "windmill_size_ft", asset.windmill_size_ft if asset else None),
            "windmill_size_ft",
        )

        status = cls._coerce_choice(
            cls._current_value(payload, "status", asset.status if asset else None),
            "status",
            allowed=cls.STATUS_OPTIONS_BY_TYPE[asset_type],
        )
        water_level = cls._coerce_choice(
            cls._current_value(payload, "water_level", asset.water_level if asset else None),
            "water_level",
            allowed=set(cls.WATER_LEVEL_OPTIONS),
        )
        material = cls._coerce_optional_text(
            cls._current_value(payload, "material", asset.material if asset else None)
        )
        if material:
            material = cls.normalize_choice(material)
        trough_size = cls._coerce_choice(
            cls._current_value(payload, "trough_size", asset.trough_size if asset else None),
            "trough_size",
            allowed=set(cls.TROUGH_SIZE_OPTIONS),
        )

        solar_brand = cls._coerce_optional_text(
            cls._current_value(payload, "solar_brand", asset.solar_brand if asset else None)
        )
        source_system = cls._coerce_optional_text(
            cls._current_value(payload, "source_system", asset.source_system if asset else None)
        )
        import_placemark_name = cls._coerce_optional_text(
            cls._current_value(
                payload,
                "import_placemark_name",
                asset.import_placemark_name if asset else None,
            )
        )
        import_style_url = cls._coerce_optional_text(
            cls._current_value(payload, "import_style_url", asset.import_style_url if asset else None)
        )

        if latitude is not None and (latitude < Decimal("-90") or latitude > Decimal("90")):
            raise ValueError("latitude must be between -90 and 90")
        if longitude is not None and (longitude < Decimal("-180") or longitude > Decimal("180")):
            raise ValueError("longitude must be between -180 and 180")
        if capacity_m3 is not None and capacity_m3 < 0:
            raise ValueError("capacity_m3 must be greater than or equal to 0")
        if solar_kw is not None and solar_kw < 0:
            raise ValueError("solar_kw must be greater than or equal to 0")
        if solar_head_m is not None and solar_head_m < 0:
            raise ValueError("solar_head_m must be greater than or equal to 0")

        if water_level and asset_type not in cls.WATER_LEVEL_TYPES:
            raise ValueError(f"water_level is not valid for asset_type {asset_type}")
        if material:
            allowed_materials = cls.MATERIAL_OPTIONS_BY_TYPE.get(asset_type)
            if not allowed_materials or material not in allowed_materials:
                raise ValueError(f"material is not valid for asset_type {asset_type}")
        if windmill_size_ft is not None and asset_type != "windmill":
            raise ValueError("windmill_size_ft is only valid for windmill assets")
        if windmill_size_ft is not None and windmill_size_ft not in {10, 12, 14}:
            raise ValueError("windmill_size_ft must be one of: 10, 12, 14")
        if trough_size and asset_type != "trough":
            raise ValueError("trough_size is only valid for trough assets")
        if asset_type != "solarpump" and any(value is not None for value in (solar_brand, solar_kw, solar_head_m)):
            raise ValueError("solar fields are only valid for solarpump assets")

        served_paddock_ids = cls._coerce_id_list(
            payload["served_paddock_ids"]
            if "served_paddock_ids" in payload
            else [link.paddock_id for link in asset.served_paddock_links]
            if asset is not None
            else []
        )
        all_paddock_ids = served_paddock_ids + ([location_paddock_id] if location_paddock_id else [])
        paddocks_by_id = cls._paddock_map_for_ids(farm_id, all_paddock_ids)
        if location_paddock_id and location_paddock_id not in paddocks_by_id:
            raise ValueError("location_paddock_id is invalid for this farm")

        if asset_type != "trough":
            if "served_paddock_ids" in payload and served_paddock_ids:
                raise ValueError("Only trough assets can serve paddocks")
            served_paddock_ids = []
        elif any(paddock_id not in paddocks_by_id for paddock_id in served_paddock_ids):
            raise ValueError("One or more served paddock IDs are invalid for this farm")

        return {
            "farm_id": farm_id,
            "name": name,
            "asset_type": asset_type,
            "active": active,
            "needs_review": needs_review,
            "location_paddock_id": location_paddock_id,
            "latitude": latitude,
            "longitude": longitude,
            "altitude_m": altitude_m,
            "status": status,
            "water_level": water_level,
            "capacity_m3": capacity_m3,
            "material": material,
            "windmill_size_ft": windmill_size_ft,
            "solar_brand": solar_brand,
            "solar_kw": solar_kw,
            "solar_head_m": solar_head_m,
            "trough_size": trough_size,
            "source_system": source_system,
            "import_placemark_name": import_placemark_name,
            "import_style_url": import_style_url,
            "served_paddock_ids": served_paddock_ids,
        }

    @classmethod
    def _sync_served_paddocks(cls, asset: WaterAsset, served_paddock_ids: list[str]) -> None:
        target_ids = list(dict.fromkeys(served_paddock_ids))
        target_id_set = set(target_ids)
        existing_by_id: dict[str, WaterAssetServedPaddock] = {}

        for link in list(asset.served_paddock_links):
            paddock_id = str(link.paddock_id)
            if paddock_id in existing_by_id:
                asset.served_paddock_links.remove(link)
                continue
            existing_by_id[paddock_id] = link

        for paddock_id, link in list(existing_by_id.items()):
            if paddock_id in target_id_set:
                continue
            asset.served_paddock_links.remove(link)
            existing_by_id.pop(paddock_id, None)

        for paddock_id in target_ids:
            if paddock_id in existing_by_id:
                continue
            link = WaterAssetServedPaddock(paddock_id=paddock_id)
            asset.served_paddock_links.append(link)
            existing_by_id[paddock_id] = link

    @classmethod
    def apply_asset_payload(
        cls,
        asset: WaterAsset,
        payload: dict,
        *,
        imported: bool = False,
    ) -> WaterAsset:
        validated = cls.validate_asset_payload(payload, asset=asset, imported=imported)
        served_paddock_ids = validated.pop("served_paddock_ids")
        for field, value in validated.items():
            setattr(asset, field, value)
        if asset.id is None:
            db.session.flush()
        cls._sync_served_paddocks(asset, served_paddock_ids)
        return asset

    @classmethod
    def create_asset(cls, payload: dict, *, imported: bool = False) -> WaterAsset:
        validated = cls.validate_asset_payload(payload, imported=imported)
        served_paddock_ids = validated.pop("served_paddock_ids")
        asset = WaterAsset(**validated)
        db.session.add(asset)
        db.session.flush()
        cls._sync_served_paddocks(asset, served_paddock_ids)
        return asset

    @classmethod
    def update_asset(cls, asset: WaterAsset, payload: dict) -> WaterAsset:
        return cls.apply_asset_payload(asset, payload)

    @classmethod
    def serialize_asset(cls, asset: WaterAsset) -> dict:
        served_links = sorted(
            asset.served_paddock_links,
            key=lambda row: row.paddock.name.lower(),
        )
        return {
            "id": str(asset.id),
            "farm_id": str(asset.farm_id),
            "name": asset.name,
            "asset_type": asset.asset_type,
            "asset_type_label": cls.ASSET_TYPE_LABELS.get(asset.asset_type, asset.asset_type),
            "active": asset.active,
            "needs_review": asset.needs_review,
            "location_paddock_id": str(asset.location_paddock_id) if asset.location_paddock_id else None,
            "location_paddock_name": asset.location_paddock.name if asset.location_paddock else None,
            "latitude": cls._to_float(asset.latitude),
            "longitude": cls._to_float(asset.longitude),
            "altitude_m": cls._to_float(asset.altitude_m),
            "status": asset.status,
            "water_level": asset.water_level,
            "capacity_m3": cls._to_float(asset.capacity_m3),
            "material": asset.material,
            "windmill_size_ft": asset.windmill_size_ft,
            "solar_brand": asset.solar_brand,
            "solar_kw": cls._to_float(asset.solar_kw),
            "solar_head_m": cls._to_float(asset.solar_head_m),
            "trough_size": asset.trough_size,
            "source_system": asset.source_system,
            "import_placemark_name": asset.import_placemark_name,
            "import_style_url": asset.import_style_url,
            "served_paddock_ids": [str(link.paddock_id) for link in served_links],
            "served_paddocks": [
                {"id": str(link.paddock_id), "name": link.paddock.name}
                for link in served_links
            ],
        }

    @classmethod
    def validate_connection_payload(
        cls,
        payload: dict,
        *,
        connection: WaterConnection | None = None,
    ) -> dict:
        farm_id = cls._current_value(payload, "farm_id", connection.farm_id if connection else None)
        if not farm_id:
            raise ValueError("farm_id is required")

        flow_type = cls._coerce_choice(
            cls._current_value(payload, "flow_type", connection.flow_type if connection else None),
            "flow_type",
            allowed=set(cls.FLOW_TYPES),
        )
        if not flow_type:
            raise ValueError("flow_type is required")

        active = cls._coerce_boolean(
            cls._current_value(payload, "active", connection.active if connection else True),
            "active",
        )
        source_asset_id = str(
            cls._current_value(payload, "source_asset_id", connection.source_asset_id if connection else "")
        ).strip()
        destination_asset_id = str(
            cls._current_value(
                payload,
                "destination_asset_id",
                connection.destination_asset_id if connection else "",
            )
        ).strip()
        raw_pump_asset_id = cls._current_value(
            payload,
            "pump_asset_id",
            connection.pump_asset_id if connection else None,
        )
        pump_asset_id = str(raw_pump_asset_id).strip() if raw_pump_asset_id else None

        if not source_asset_id or not destination_asset_id:
            raise ValueError("source_asset_id and destination_asset_id are required")
        if source_asset_id == destination_asset_id:
            raise ValueError("source_asset_id and destination_asset_id must differ")

        asset_ids = [source_asset_id, destination_asset_id]
        if pump_asset_id:
            asset_ids.append(pump_asset_id)
        assets = WaterAsset.query.filter(WaterAsset.farm_id == farm_id, WaterAsset.id.in_(asset_ids)).all()
        assets_by_id = {str(asset.id): asset for asset in assets}
        if source_asset_id not in assets_by_id:
            raise ValueError("source_asset_id is invalid for this farm")
        if destination_asset_id not in assets_by_id:
            raise ValueError("destination_asset_id is invalid for this farm")
        if pump_asset_id and pump_asset_id not in assets_by_id:
            raise ValueError("pump_asset_id is invalid for this farm")

        source_asset = assets_by_id[source_asset_id]
        destination_asset = assets_by_id[destination_asset_id]
        pump_asset = assets_by_id.get(pump_asset_id) if pump_asset_id else None

        if flow_type == "gravity":
            if pump_asset_id:
                raise ValueError("pump_asset_id must be null for gravity connections")
            if source_asset.asset_type not in cls.GRAVITY_SOURCE_TYPES or destination_asset.asset_type not in cls.GRAVITY_DESTINATION_TYPES:
                raise ValueError("gravity only allows cement_dam|tank -> trough")
        else:
            if not pump_asset_id:
                raise ValueError("pump_asset_id is required for pumped connections")
            if source_asset.asset_type not in cls.PUMPED_SOURCE_TYPES or destination_asset.asset_type not in cls.PUMPED_DESTINATION_TYPES:
                raise ValueError(
                    "pumped only allows borehole|pit|cement_dam|tank|ground_dam|weir -> cement_dam|tank"
                )
            if pump_asset and pump_asset.asset_type not in cls.PUMP_ASSET_TYPES:
                raise ValueError("pump_asset_id must reference a windmill or solarpump asset")
            if pump_asset_id in {source_asset_id, destination_asset_id}:
                raise ValueError("pump_asset_id must reference a separate pump asset")
            existing_pump_connection = WaterConnection.query.filter_by(pump_asset_id=pump_asset_id).first()
            if existing_pump_connection and (
                connection is None or str(existing_pump_connection.id) != str(connection.id)
            ):
                raise ValueError("pump_asset_id is already used by another pumped connection")

        pipe_material = cls._coerce_optional_text(
            cls._current_value(payload, "pipe_material", connection.pipe_material if connection else None)
        )
        if pipe_material:
            pipe_material = cls.normalize_choice(pipe_material)
        notes = cls._coerce_optional_text(
            cls._current_value(payload, "notes", connection.notes if connection else None)
        )

        return {
            "farm_id": farm_id,
            "active": active,
            "flow_type": flow_type,
            "source_asset_id": source_asset_id,
            "destination_asset_id": destination_asset_id,
            "pump_asset_id": pump_asset_id if flow_type == "pumped" else None,
            "pipe_material": pipe_material,
            "pipe_diameter_spec": cls._coerce_optional_text(
                cls._current_value(
                    payload,
                    "pipe_diameter_spec",
                    connection.pipe_diameter_spec if connection else None,
                )
            ),
            "pipe_wall_thickness_spec": cls._coerce_optional_text(
                cls._current_value(
                    payload,
                    "pipe_wall_thickness_spec",
                    connection.pipe_wall_thickness_spec if connection else None,
                )
            ),
            "pipe_class_spec": cls._coerce_optional_text(
                cls._current_value(
                    payload,
                    "pipe_class_spec",
                    connection.pipe_class_spec if connection else None,
                )
            ),
            "pipe_quality_spec": cls._coerce_optional_text(
                cls._current_value(
                    payload,
                    "pipe_quality_spec",
                    connection.pipe_quality_spec if connection else None,
                )
            ),
            "notes": notes,
        }

    @classmethod
    def create_connection(cls, payload: dict) -> WaterConnection:
        validated = cls.validate_connection_payload(payload)
        connection = WaterConnection(**validated)
        db.session.add(connection)
        return connection

    @classmethod
    def update_connection(cls, connection: WaterConnection, payload: dict) -> WaterConnection:
        validated = cls.validate_connection_payload(payload, connection=connection)
        for field, value in validated.items():
            setattr(connection, field, value)
        return connection

    @classmethod
    def serialize_connection(cls, connection: WaterConnection) -> dict:
        return {
            "id": str(connection.id),
            "farm_id": str(connection.farm_id),
            "active": connection.active,
            "flow_type": connection.flow_type,
            "flow_type_label": cls.FLOW_TYPE_LABELS.get(connection.flow_type, connection.flow_type),
            "source_asset_id": str(connection.source_asset_id),
            "source_asset_name": connection.source_asset.name if connection.source_asset else None,
            "source_asset_type": connection.source_asset.asset_type if connection.source_asset else None,
            "destination_asset_id": str(connection.destination_asset_id),
            "destination_asset_name": connection.destination_asset.name if connection.destination_asset else None,
            "destination_asset_type": (
                connection.destination_asset.asset_type if connection.destination_asset else None
            ),
            "pump_asset_id": str(connection.pump_asset_id) if connection.pump_asset_id else None,
            "pump_asset_name": connection.pump_asset.name if connection.pump_asset else None,
            "pump_asset_type": connection.pump_asset.asset_type if connection.pump_asset else None,
            "pipe_material": connection.pipe_material,
            "pipe_diameter_spec": connection.pipe_diameter_spec,
            "pipe_wall_thickness_spec": connection.pipe_wall_thickness_spec,
            "pipe_class_spec": connection.pipe_class_spec,
            "pipe_quality_spec": connection.pipe_quality_spec,
            "notes": connection.notes,
        }

    @classmethod
    def asset_map_feature(cls, asset: WaterAsset) -> dict | None:
        if not asset.active or asset.latitude is None or asset.longitude is None:
            return None
        properties = cls.serialize_asset(asset)
        properties["feature_type"] = "water_asset"
        return {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [float(asset.longitude), float(asset.latitude)],
            },
            "properties": properties,
        }

    @classmethod
    def connection_map_feature(cls, connection: WaterConnection) -> dict | None:
        source_asset = connection.source_asset
        destination_asset = connection.destination_asset
        if (
            not connection.active
            or source_asset is None
            or destination_asset is None
            or not source_asset.active
            or not destination_asset.active
            or source_asset.latitude is None
            or source_asset.longitude is None
            or destination_asset.latitude is None
            or destination_asset.longitude is None
        ):
            return None

        properties = cls.serialize_connection(connection)
        properties["feature_type"] = "water_connection"
        return {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    [float(source_asset.longitude), float(source_asset.latitude)],
                    [float(destination_asset.longitude), float(destination_asset.latitude)],
                ],
            },
            "properties": properties,
        }

    @classmethod
    def active_map_features_for_farm(cls, farm_id: str) -> list[dict]:
        assets = (
            WaterAsset.query.filter_by(farm_id=farm_id)
            .order_by(WaterAsset.asset_type.asc(), WaterAsset.name.asc())
            .all()
        )
        features = [feature for asset in assets if (feature := cls.asset_map_feature(asset))]

        connections = (
            WaterConnection.query.filter_by(farm_id=farm_id)
            .order_by(WaterConnection.flow_type.asc(), WaterConnection.created_at.asc())
            .all()
        )
        features.extend(
            feature for connection in connections if (feature := cls.connection_map_feature(connection))
        )
        return features

    @classmethod
    def farm_summary(cls, farm_id: str) -> dict:
        assets = WaterAsset.query.filter_by(farm_id=farm_id).all()
        active_assets = [asset for asset in assets if asset.active]
        connections = WaterConnection.query.filter_by(farm_id=farm_id).all()
        active_connections = [connection for connection in connections if connection.active]
        return {
            "active_asset_count": len(active_assets),
            "review_asset_count": len([asset for asset in active_assets if asset.needs_review]),
            "active_connection_count": len(active_connections),
            "trough_count": len([asset for asset in active_assets if asset.asset_type == "trough"]),
        }

    @classmethod
    def assets_for_farm(cls, farm_id: str) -> list[WaterAsset]:
        return (
            WaterAsset.query.filter_by(farm_id=farm_id)
            .order_by(WaterAsset.asset_type.asc(), WaterAsset.name.asc())
            .all()
        )

    @classmethod
    def connections_for_farm(cls, farm_id: str) -> list[WaterConnection]:
        return (
            WaterConnection.query.filter_by(farm_id=farm_id)
            .order_by(WaterConnection.flow_type.asc(), WaterConnection.created_at.asc())
            .all()
        )

    @classmethod
    def local_assets_for_paddock(cls, paddock_id: str) -> list[WaterAsset]:
        return (
            WaterAsset.query.filter_by(location_paddock_id=paddock_id, active=True)
            .order_by(WaterAsset.asset_type.asc(), WaterAsset.name.asc())
            .all()
        )

    @classmethod
    def troughs_serving_paddock(cls, paddock_id: str) -> list[WaterAsset]:
        return (
            WaterAsset.query.join(WaterAssetServedPaddock)
            .filter(
                WaterAssetServedPaddock.paddock_id == paddock_id,
                WaterAsset.asset_type == "trough",
                WaterAsset.active.is_(True),
            )
            .order_by(WaterAsset.name.asc())
            .all()
        )
