from collections import defaultdict
from decimal import Decimal, InvalidOperation
from math import cos, radians

from sqlalchemy import or_

from app.extensions import db
from app.models import Paddock, WaterAsset, WaterAssetServedPaddock, WaterConnection
from app.models.water import WATER_ASSET_TYPES, WATER_CONNECTION_FLOW_TYPES
from app.services.paddock_service import PaddockService


class WaterNetworkService:
    MAX_NAME_LENGTH = 120
    EARTH_RADIUS_M = 6371008.8

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
        "weir": "Weir",
        "trough": "Trough",
    }
    FLOW_TYPE_LABELS = {"pumped": "Pumped", "gravity": "Gravity"}
    PUMP_ASSET_TYPES = {"windmill", "solarpump"}
    DEFAULT_TROUGH_CONNECTION_PIPE_MATERIAL = "plastic"
    DEFAULT_TROUGH_CONNECTION_PIPE_DIAMETER_SPEC = "32mm"
    DEFAULT_TROUGH_CONNECTION_PIPE_CLASS_SPEC = "class 2"
    DEFAULT_TROUGH_CONNECTION_SOURCE_TYPES = {"cement_dam"}
    IMPORT_DEFAULT_TROUGH_CONNECTION_SOURCE_TYPES = {"cement_dam", "tank"}
    STORAGE_ASSET_TYPES = {"cement_dam", "tank", "ground_dam"}
    WATER_LEVEL_TYPES = {"pit", "cement_dam", "tank", "ground_dam", "weir", "trough"}
    WATER_LEVEL_OPTIONS = ("empty", "low", "half", "high", "full")
    WATER_LEVEL_ALERT_EXCLUDED_TYPES = {"weir"}
    CAPACITY_TYPES = WATER_LEVEL_TYPES
    SERVED_PADDOCK_TYPES = {"ground_dam", "weir", "trough"}
    LOCATION_BOUND_SERVED_PADDOCK_TYPES = {"ground_dam", "weir"}
    WINDMILL_SIZE_OPTIONS = (10, 12, 14)
    WEIR_SIZE_OPTIONS = ("small", "medium", "large")
    TROUGH_SIZE_OPTIONS = ("small", "medium", "large")
    DEFAULT_VALUES_BY_TYPE = {
        "weir": {
            "status": "operational",
            "water_level": "empty",
            "weir_size": "medium",
        }
    }
    MATERIAL_OPTIONS_BY_TYPE = {
        "pit": {"earth"},
        "cement_dam": {"brick", "stone"},
        "tank": {"concrete", "plastic", "steel"},
        "ground_dam": {"earth"},
        "trough": {"concrete", "plastic", "steel", "rubber"},
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
    DOWN_PUMP_STATUSES = {"down"}
    DRY_SOURCE_STATUSES = {"dry"}
    GRAVITY_TROUGH_SOURCE_TYPES = {"cement_dam", "ground_dam", "pit", "tank", "weir"}
    TRANSFER_SOURCE_TYPES = {"borehole", "cement_dam", "ground_dam", "pit", "tank", "weir"}
    TRANSFER_DESTINATION_TYPES = {"borehole", "cement_dam", "pit", "tank"}

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
        def retained_value(field: str, current, *, keep_current: bool = True):
            fallback = current if keep_current else None
            return cls._current_value(payload, field, fallback)

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
        status_options = cls.STATUS_OPTIONS_BY_TYPE[asset_type]
        material_options = cls.MATERIAL_OPTIONS_BY_TYPE.get(asset_type)
        water_level_options = set(cls.WATER_LEVEL_OPTIONS)
        windmill_size_options = set(cls.WINDMILL_SIZE_OPTIONS)
        weir_size_options = set(cls.WEIR_SIZE_OPTIONS)
        trough_size_options = set(cls.TROUGH_SIZE_OPTIONS)

        capacity_m3 = cls._coerce_optional_decimal(
            retained_value(
                "capacity_m3",
                asset.capacity_m3 if asset else None,
                keep_current=asset is not None and asset_type in cls.CAPACITY_TYPES,
            ),
            "capacity_m3",
        )
        solar_kw = cls._coerce_optional_decimal(
            retained_value(
                "solar_kw",
                asset.solar_kw if asset else None,
                keep_current=asset is not None and asset_type == "solarpump",
            ),
            "solar_kw",
        )
        solar_head_m = cls._coerce_optional_decimal(
            retained_value(
                "solar_head_m",
                asset.solar_head_m if asset else None,
                keep_current=asset is not None and asset_type == "solarpump",
            ),
            "solar_head_m",
        )
        windmill_size_ft = cls._coerce_optional_integer(
            retained_value(
                "windmill_size_ft",
                asset.windmill_size_ft if asset else None,
                keep_current=asset is not None and asset_type == "windmill",
            ),
            "windmill_size_ft",
        )
        weir_size = cls._coerce_choice(
            retained_value(
                "weir_size",
                asset.weir_size if asset else None,
                keep_current=asset is not None and asset_type == "weir",
            ),
            "weir_size",
            allowed=weir_size_options,
        )

        status = cls._coerce_choice(
            retained_value(
                "status",
                asset.status if asset else None,
                keep_current=asset is not None and asset.status in status_options,
            ),
            "status",
            allowed=status_options,
        )
        water_level = cls._coerce_choice(
            retained_value(
                "water_level",
                asset.water_level if asset else None,
                keep_current=asset is not None and asset_type in cls.WATER_LEVEL_TYPES,
            ),
            "water_level",
            allowed=water_level_options,
        )
        material = cls._coerce_optional_text(
            retained_value(
                "material",
                asset.material if asset else None,
                keep_current=asset is not None
                and material_options is not None
                and asset.material in material_options,
            )
        )
        if material:
            material = cls.normalize_choice(material)

        if asset_type == "weir":
            weir_defaults = cls.DEFAULT_VALUES_BY_TYPE["weir"]
            if status is None:
                status = weir_defaults["status"]
            if water_level is None:
                water_level = weir_defaults["water_level"]
            if weir_size is None:
                weir_size = weir_defaults["weir_size"]

        trough_size = cls._coerce_choice(
            retained_value(
                "trough_size",
                asset.trough_size if asset else None,
                keep_current=asset is not None and asset_type == "trough",
            ),
            "trough_size",
            allowed=trough_size_options,
        )

        solar_brand = cls._coerce_optional_text(
            retained_value(
                "solar_brand",
                asset.solar_brand if asset else None,
                keep_current=asset is not None and asset_type == "solarpump",
            )
        )
        source_system = cls._coerce_optional_text(
            retained_value(
                "source_system",
                asset.source_system if asset else None,
                keep_current=asset is not None and asset_type == "solarpump",
            )
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
        if capacity_m3 is not None and asset_type not in cls.CAPACITY_TYPES:
            raise ValueError(f"capacity_m3 is not valid for asset_type {asset_type}")
        if material:
            if not material_options or material not in material_options:
                raise ValueError(f"material is not valid for asset_type {asset_type}")
        if windmill_size_ft is not None and asset_type != "windmill":
            raise ValueError("windmill_size_ft is only valid for windmill assets")
        if windmill_size_ft is not None and windmill_size_ft not in windmill_size_options:
            allowed_sizes = ", ".join(str(size) for size in cls.WINDMILL_SIZE_OPTIONS)
            raise ValueError(f"windmill_size_ft must be one of: {allowed_sizes}")
        if weir_size and asset_type != "weir":
            raise ValueError("weir_size is only valid for weir assets")
        if trough_size and asset_type != "trough":
            raise ValueError("trough_size is only valid for trough assets")
        if asset_type != "solarpump" and any(value is not None for value in (solar_brand, solar_kw, solar_head_m)):
            raise ValueError("solar fields are only valid for solarpump assets")
        if asset_type != "solarpump" and source_system is not None:
            raise ValueError("source_system is only valid for solarpump assets")

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

        if asset_type in cls.LOCATION_BOUND_SERVED_PADDOCK_TYPES:
            served_paddock_ids = [location_paddock_id] if location_paddock_id else []
        elif asset_type not in cls.SERVED_PADDOCK_TYPES:
            if "served_paddock_ids" in payload and served_paddock_ids:
                raise ValueError("Only ground_dam, weir, and trough assets can serve paddocks")
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
            "weir_size": weir_size,
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

    @staticmethod
    def _asset_coordinates(asset: WaterAsset) -> tuple[float, float] | None:
        if asset.latitude is None or asset.longitude is None:
            return None
        return (float(asset.longitude), float(asset.latitude))

    @classmethod
    def _distance_between_coordinates_m(
        cls,
        left: tuple[float, float],
        right: tuple[float, float],
    ) -> float:
        average_lat = radians((left[1] + right[1]) / 2.0)
        dx = cls.EARTH_RADIUS_M * radians(right[0] - left[0]) * cos(average_lat)
        dy = cls.EARTH_RADIUS_M * radians(right[1] - left[1])
        return (dx * dx + dy * dy) ** 0.5

    @classmethod
    def ensure_default_trough_connections(
        cls,
        farm_id: str,
        *,
        source_asset_types: set[str] | None = None,
    ) -> list[WaterConnection]:
        assets = cls.assets_for_farm(farm_id)
        if not assets:
            return []

        target_source_types = (
            set(cls.DEFAULT_TROUGH_CONNECTION_SOURCE_TYPES)
            if source_asset_types is None
            else set(source_asset_types)
        )
        active_sources = [
            asset
            for asset in assets
            if asset.active
            and asset.asset_type in target_source_types
            and cls._asset_coordinates(asset) is not None
        ]
        if not active_sources:
            return []

        connections = WaterConnection.query.filter_by(farm_id=farm_id).all()
        connections_by_destination: dict[str, list[WaterConnection]] = defaultdict(list)
        for connection in connections:
            destination_asset_id = str(connection.destination_asset_id)
            connections_by_destination[destination_asset_id].append(connection)

        changed_connections = []
        for trough in assets:
            if not trough.active or trough.asset_type != "trough":
                continue

            trough_id = str(trough.id)
            if connections_by_destination.get(trough_id):
                continue

            trough_coordinates = cls._asset_coordinates(trough)
            if trough_coordinates is None:
                continue

            nearest_source_asset = min(
                active_sources,
                key=lambda source_asset: cls._distance_between_coordinates_m(
                    trough_coordinates,
                    cls._asset_coordinates(source_asset),
                ),
            )
            connection = WaterConnection(
                farm_id=farm_id,
                active=True,
                flow_type="gravity",
                source_asset_id=nearest_source_asset.id,
                destination_asset_id=trough.id,
                pipe_material=cls.DEFAULT_TROUGH_CONNECTION_PIPE_MATERIAL,
                pipe_diameter_spec=cls.DEFAULT_TROUGH_CONNECTION_PIPE_DIAMETER_SPEC,
                pipe_class_spec=cls.DEFAULT_TROUGH_CONNECTION_PIPE_CLASS_SPEC,
            )
            db.session.add(connection)
            connections_by_destination[trough_id].append(connection)
            changed_connections.append(connection)

        if changed_connections:
            db.session.flush()
        return changed_connections

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
        if not imported:
            cls.ensure_default_trough_connections(validated["farm_id"])
        return asset

    @classmethod
    def create_asset(cls, payload: dict, *, imported: bool = False) -> WaterAsset:
        validated = cls.validate_asset_payload(payload, imported=imported)
        served_paddock_ids = validated.pop("served_paddock_ids")
        asset = WaterAsset(**validated)
        db.session.add(asset)
        db.session.flush()
        cls._sync_served_paddocks(asset, served_paddock_ids)
        if not imported:
            cls.ensure_default_trough_connections(validated["farm_id"])
        return asset

    @classmethod
    def update_asset(cls, asset: WaterAsset, payload: dict) -> WaterAsset:
        return cls.apply_asset_payload(asset, payload)

    @classmethod
    def delete_assets(cls, farm_id: str, asset_ids: list[str]) -> list[WaterAsset]:
        unique_asset_ids = []
        seen_asset_ids = set()
        for asset_id in asset_ids:
            normalized_asset_id = str(asset_id or "").strip()
            if not normalized_asset_id or normalized_asset_id in seen_asset_ids:
                continue
            seen_asset_ids.add(normalized_asset_id)
            unique_asset_ids.append(normalized_asset_id)

        if not unique_asset_ids:
            return []

        assets = WaterAsset.query.filter(
            WaterAsset.farm_id == farm_id,
            WaterAsset.id.in_(unique_asset_ids),
        ).all()
        if len(assets) != len(unique_asset_ids):
            raise ValueError("One or more selected water assets are invalid for this farm")

        WaterConnection.query.filter(
            WaterConnection.farm_id == farm_id,
            or_(
                WaterConnection.source_asset_id.in_(unique_asset_ids),
                WaterConnection.destination_asset_id.in_(unique_asset_ids),
                WaterConnection.pump_asset_id.in_(unique_asset_ids),
            ),
        ).delete(synchronize_session=False)
        for asset in assets:
            db.session.delete(asset)
        db.session.flush()
        cls.ensure_default_trough_connections(
            farm_id,
            source_asset_types=cls.IMPORT_DEFAULT_TROUGH_CONNECTION_SOURCE_TYPES,
        )
        return assets

    @classmethod
    def _allows_gravity_connection(cls, source_asset_type: str, destination_asset_type: str) -> bool:
        if destination_asset_type == "trough":
            return source_asset_type in cls.GRAVITY_TROUGH_SOURCE_TYPES
        return (
            source_asset_type in cls.TRANSFER_SOURCE_TYPES
            and destination_asset_type in cls.TRANSFER_DESTINATION_TYPES
        )

    @classmethod
    def _allows_pumped_connection(cls, source_asset_type: str, destination_asset_type: str) -> bool:
        return (
            source_asset_type in cls.TRANSFER_SOURCE_TYPES
            and destination_asset_type in cls.TRANSFER_DESTINATION_TYPES
        )

    @classmethod
    def _is_pump_operational(cls, asset: WaterAsset | None) -> bool:
        if asset is None or not asset.active or asset.asset_type not in cls.PUMP_ASSET_TYPES:
            return False
        return cls.normalize_choice(asset.status) not in cls.DOWN_PUMP_STATUSES

    @classmethod
    def _normalized_water_level(cls, value: str | None) -> str | None:
        normalized_value = cls.normalize_choice(value)
        return normalized_value if normalized_value in cls.WATER_LEVEL_OPTIONS else None

    @classmethod
    def _highest_known_water_level(cls, levels: list[str]) -> str | None:
        normalized_levels = [
            normalized_level
            for normalized_level in (cls._normalized_water_level(level) for level in levels)
            if normalized_level is not None
        ]
        if not normalized_levels:
            return None
        return max(normalized_levels, key=lambda level: cls.WATER_LEVEL_OPTIONS.index(level))

    @classmethod
    def _asset_supply_state(cls, asset: WaterAsset) -> str:
        if not asset.active:
            return "empty"

        normalized_status = cls.normalize_choice(asset.status)
        if normalized_status in cls.DRY_SOURCE_STATUSES:
            return "empty"

        if asset.asset_type == "borehole":
            return "available"

        if asset.asset_type in cls.WATER_LEVEL_TYPES:
            normalized_level = cls.normalize_choice(asset.water_level)
            if normalized_level == "empty":
                return "empty"
            if normalized_level:
                return "available"
            return "unknown"

        return "available"

    @classmethod
    def _upstream_supply_paths(
        cls,
        asset_id: str,
        incoming_connections_by_destination: dict[str, list[WaterConnection]],
        active_assets_by_id: dict[str, WaterAsset],
        *,
        trail: set[str] | None = None,
    ) -> list[dict]:
        trail = trail or set()
        if asset_id in trail:
            return []

        next_trail = set(trail)
        next_trail.add(asset_id)
        incoming_connections = incoming_connections_by_destination.get(asset_id, [])
        if not incoming_connections:
            return [
                {
                    "pump_ids": set(),
                    "operational_pump_ids": set(),
                    "down_pump_ids": set(),
                }
            ]

        paths = []
        for connection in incoming_connections:
            source_asset_id = str(connection.source_asset_id)
            if source_asset_id not in active_assets_by_id:
                continue

            upstream_paths = cls._upstream_supply_paths(
                source_asset_id,
                incoming_connections_by_destination,
                active_assets_by_id,
                trail=next_trail,
            )
            if not upstream_paths:
                continue

            edge_pump_ids = set()
            edge_operational_pump_ids = set()
            edge_down_pump_ids = set()
            if connection.flow_type == "pumped" and connection.pump_asset_id:
                pump_asset_id = str(connection.pump_asset_id)
                edge_pump_ids.add(pump_asset_id)
                if cls._is_pump_operational(connection.pump_asset):
                    edge_operational_pump_ids.add(pump_asset_id)
                else:
                    edge_down_pump_ids.add(pump_asset_id)

            for path in upstream_paths:
                paths.append(
                    {
                        "pump_ids": set(path["pump_ids"]) | edge_pump_ids,
                        "operational_pump_ids": set(path["operational_pump_ids"])
                        | edge_operational_pump_ids,
                        "down_pump_ids": set(path["down_pump_ids"]) | edge_down_pump_ids,
                    }
                )
        return paths

    @classmethod
    def network_state_for_farm(
        cls,
        farm_id: str,
        *,
        assets: list[WaterAsset] | None = None,
        connections: list[WaterConnection] | None = None,
    ) -> dict:
        if assets is None:
            assets = cls.assets_for_farm(farm_id)
        if connections is None:
            connections = cls.connections_for_farm(farm_id)

        active_assets_by_id = {str(asset.id): asset for asset in assets if asset.active}
        incoming_connections_by_destination: dict[str, list[WaterConnection]] = defaultdict(list)

        for connection in connections:
            if not connection.active:
                continue
            source_asset_id = str(connection.source_asset_id)
            destination_asset_id = str(connection.destination_asset_id)
            if source_asset_id not in active_assets_by_id or destination_asset_id not in active_assets_by_id:
                continue
            incoming_connections_by_destination[destination_asset_id].append(connection)

        effective_water_levels = {str(asset.id): asset.water_level for asset in assets}
        asset_warnings: dict[str, str | None] = {str(asset.id): None for asset in assets}
        served_asset_supply_summaries: dict[str, dict] = {}

        for asset in assets:
            asset_id = str(asset.id)
            if not asset.active or asset.asset_type not in cls.SERVED_PADDOCK_TYPES:
                continue

            incoming_connections = incoming_connections_by_destination.get(asset_id, [])
            has_available_upstream_source = False
            has_unknown_upstream_source = False
            unavailable_source_names = []
            for connection in incoming_connections:
                source_asset = active_assets_by_id.get(str(connection.source_asset_id))
                if source_asset is None:
                    continue
                source_state = cls._asset_supply_state(source_asset)
                if source_state == "available":
                    has_available_upstream_source = True
                elif source_state == "empty":
                    unavailable_source_names.append(source_asset.name)
                else:
                    has_unknown_upstream_source = True

            if (
                incoming_connections
                and not has_available_upstream_source
                and not has_unknown_upstream_source
                and unavailable_source_names
            ):
                effective_water_levels[asset_id] = "empty"
                source_names = ", ".join(
                    sorted({name for name in unavailable_source_names}, key=lambda value: value.lower())
                )
                asset_warnings[asset_id] = f"Upstream source is empty or unavailable: {source_names}."

            if asset.asset_type == "trough" and cls.normalize_choice(asset.status) == "operational":
                source_water_levels = []
                for connection in incoming_connections:
                    source_asset_id = str(connection.source_asset_id)
                    source_asset = active_assets_by_id.get(source_asset_id)
                    if source_asset is None:
                        continue
                    source_water_levels.append(
                        effective_water_levels.get(source_asset_id, source_asset.water_level)
                    )
                mirrored_water_level = cls._highest_known_water_level(source_water_levels)
                if mirrored_water_level is not None:
                    effective_water_levels[asset_id] = mirrored_water_level

            upstream_paths = cls._upstream_supply_paths(
                asset_id,
                incoming_connections_by_destination,
                active_assets_by_id,
            )
            required_pump_ids = set()
            operational_pump_ids = set()
            down_pump_ids = set()
            has_operational_path = False

            for path in upstream_paths:
                path_pump_ids = set(path["pump_ids"])
                path_operational_pump_ids = set(path["operational_pump_ids"])
                required_pump_ids.update(path_pump_ids)
                operational_pump_ids.update(path_operational_pump_ids)
                down_pump_ids.update(path["down_pump_ids"])
                if path_pump_ids.issubset(path_operational_pump_ids):
                    has_operational_path = True

            served_asset_supply_summaries[asset_id] = {
                "required_pump_ids": required_pump_ids,
                "operational_pump_ids": operational_pump_ids,
                "down_pump_ids": down_pump_ids,
                "has_operational_path": has_operational_path,
                "effective_water_level": effective_water_levels.get(asset_id),
                "warning": asset_warnings.get(asset_id),
            }

        served_assets_by_paddock: dict[str, list[WaterAsset]] = defaultdict(list)
        for asset in assets:
            if not asset.active or asset.asset_type not in cls.SERVED_PADDOCK_TYPES:
                continue
            for link in asset.served_paddock_links:
                served_assets_by_paddock[str(link.paddock_id)].append(asset)

        paddock_alerts = {}
        for paddock_id, service_assets in served_assets_by_paddock.items():
            required_pump_ids = set()
            operational_pump_ids = set()
            down_pump_ids = set()
            has_operational_path = False
            level_alert_service_assets = []
            empty_service_asset_notes = []

            for service_asset in service_assets:
                service_asset_id = str(service_asset.id)
                summary = served_asset_supply_summaries.get(
                    service_asset_id,
                    {
                        "required_pump_ids": set(),
                        "operational_pump_ids": set(),
                        "down_pump_ids": set(),
                        "has_operational_path": True,
                        "effective_water_level": effective_water_levels.get(service_asset_id),
                        "warning": asset_warnings.get(service_asset_id),
                    },
                )
                required_pump_ids.update(summary["required_pump_ids"])
                operational_pump_ids.update(summary["operational_pump_ids"])
                down_pump_ids.update(summary["down_pump_ids"])
                has_operational_path = has_operational_path or bool(summary["has_operational_path"])

                if service_asset.asset_type in cls.WATER_LEVEL_ALERT_EXCLUDED_TYPES:
                    continue

                level_alert_service_assets.append(service_asset)
                if cls.normalize_choice(summary["effective_water_level"]) == "empty":
                    note = service_asset.name
                    if summary["warning"]:
                        note = f"{note} ({summary['warning']})"
                    empty_service_asset_notes.append(note)

            if level_alert_service_assets and len(empty_service_asset_notes) == len(level_alert_service_assets):
                message = "All monitored served water points are empty: " + "; ".join(empty_service_asset_notes)
                paddock_alerts[paddock_id] = {
                    "level": "critical",
                    "message": message,
                }
                continue

            if required_pump_ids and not has_operational_path:
                down_pump_names = sorted(
                    {
                        active_assets_by_id[pump_id].name
                        for pump_id in down_pump_ids
                        if pump_id in active_assets_by_id
                    },
                    key=lambda value: value.lower(),
                )
                pump_suffix = (
                    " Down pumps: " + ", ".join(down_pump_names) + "."
                    if down_pump_names
                    else ""
                )
                paddock_alerts[paddock_id] = {
                    "level": "critical",
                    "message": "No working pumped supply path reaches this paddock." + pump_suffix,
                }

        return {
            "effective_water_levels": effective_water_levels,
            "asset_warnings": asset_warnings,
            "served_asset_supply_summaries": served_asset_supply_summaries,
            "paddock_alerts": paddock_alerts,
        }

    @classmethod
    def serialize_asset(cls, asset: WaterAsset, *, network_state: dict | None = None) -> dict:
        served_links = sorted(
            asset.served_paddock_links,
            key=lambda row: row.paddock.name.lower(),
        )
        asset_id = str(asset.id)
        effective_water_levels = (network_state or {}).get("effective_water_levels", {})
        asset_warnings = (network_state or {}).get("asset_warnings", {})
        return {
            "id": asset_id,
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
            "water_level": effective_water_levels.get(asset_id, asset.water_level),
            "reported_water_level": asset.water_level,
            "capacity_m3": cls._to_float(asset.capacity_m3),
            "material": asset.material,
            "windmill_size_ft": asset.windmill_size_ft,
            "weir_size": asset.weir_size,
            "solar_brand": asset.solar_brand,
            "solar_kw": cls._to_float(asset.solar_kw),
            "solar_head_m": cls._to_float(asset.solar_head_m),
            "trough_size": asset.trough_size,
            "source_system": asset.source_system,
            "import_placemark_name": asset.import_placemark_name,
            "import_style_url": asset.import_style_url,
            "network_warning": asset_warnings.get(asset_id),
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
            if not cls._allows_gravity_connection(
                source_asset.asset_type,
                destination_asset.asset_type,
            ):
                raise ValueError(
                    "gravity only allows cement_dam|ground_dam|pit|tank|weir -> trough "
                    "or transfers from borehole|cement_dam|ground_dam|pit|tank|weir into "
                    "borehole|cement_dam|pit|tank"
                )
        else:
            if not pump_asset_id:
                raise ValueError("pump_asset_id is required for pumped connections")
            if not cls._allows_pumped_connection(
                source_asset.asset_type,
                destination_asset.asset_type,
            ):
                raise ValueError(
                    "pumped only allows transfers from borehole|cement_dam|ground_dam|pit|tank|weir "
                    "into borehole|cement_dam|pit|tank"
                )
            if pump_asset and pump_asset.asset_type not in cls.PUMP_ASSET_TYPES:
                raise ValueError("pump_asset_id must reference a windmill or solarpump asset")
            if pump_asset_id in {source_asset_id, destination_asset_id}:
                raise ValueError("pump_asset_id must reference a separate pump asset")

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
    def asset_map_feature(cls, asset: WaterAsset, *, network_state: dict | None = None) -> dict | None:
        if not asset.active or asset.latitude is None or asset.longitude is None:
            return None
        properties = cls.serialize_asset(asset, network_state=network_state)
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
    def active_map_features_for_farm(
        cls,
        farm_id: str,
        *,
        network_state: dict | None = None,
    ) -> list[dict]:
        assets = (
            WaterAsset.query.filter_by(farm_id=farm_id)
            .order_by(WaterAsset.asset_type.asc(), WaterAsset.name.asc())
            .all()
        )
        connections = (
            WaterConnection.query.filter_by(farm_id=farm_id)
            .order_by(WaterConnection.flow_type.asc(), WaterConnection.created_at.asc())
            .all()
        )
        if network_state is None:
            network_state = cls.network_state_for_farm(farm_id, assets=assets, connections=connections)
        features = [
            feature
            for asset in assets
            if (feature := cls.asset_map_feature(asset, network_state=network_state))
        ]
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
    def service_assets_serving_paddock(cls, paddock_id: str) -> list[WaterAsset]:
        return (
            WaterAsset.query.join(WaterAssetServedPaddock)
            .filter(
                WaterAssetServedPaddock.paddock_id == paddock_id,
                WaterAsset.asset_type.in_(cls.SERVED_PADDOCK_TYPES),
                WaterAsset.active.is_(True),
            )
            .order_by(WaterAsset.name.asc())
            .all()
        )
