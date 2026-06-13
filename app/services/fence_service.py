from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from math import cos, isfinite, radians
from pathlib import Path
import json
import uuid
import xml.etree.ElementTree as ET

from app.extensions import db
from app.models import Farm, FenceEvent, FenceEventMaterial, FenceSection, Paddock
from app.services.mob_event_service import MobEventService
from app.services.paddock_service import PaddockService
from app.services.task_service import TaskService

KML_NAMESPACE = "http://www.opengis.net/kml/2.2"
KML_NAMESPACES = {"k": KML_NAMESPACE}
EARTH_RADIUS_M = 6371008.8
BOUNDARY_TOLERANCE_M = 1.0
MIN_FENCE_SECTION_LENGTH_M = 0.5


class FenceService:
    CONDITIONS = ("unknown", "good", "fair", "bad", "critical")
    CONDITION_LABELS = {
        "unknown": "Unknown",
        "good": "Good",
        "fair": "Fair",
        "bad": "Bad",
        "critical": "Critical",
    }
    HEIGHT_PROFILES = ("low", "high")
    HEIGHT_LABELS = {"low": "Low", "high": "High"}
    CONSTRUCTION_TYPES = ("high_strung_wire", "barbed_wire", "mesh", "mixed", "other")
    CONSTRUCTION_LABELS = {
        "high_strung_wire": "High Strung Wire",
        "barbed_wire": "Barbed Wire",
        "mesh": "Mesh",
        "mixed": "Mixed",
        "other": "Other",
    }
    POST_TYPES = ("unknown", "wood", "iron", "mixed", "none")
    DROPPER_TYPES = ("unknown", "wood", "iron", "mixed", "none")
    WIRE_TYPES = ("unknown", "plain", "high_strung", "barbed", "electric", "mixed")
    MESH_TYPES = ("unknown", "none", "wire_mesh", "diamond_mesh", "other")
    SUITABILITY_VALUES = ("unknown", "not_suitable", "partial", "suitable")
    SUITABILITY_LABELS = {
        "unknown": "Unknown",
        "not_suitable": "Not Suitable",
        "partial": "Partial",
        "suitable": "Suitable",
    }
    EVENT_TYPES = ("note", "inspection", "maintenance")
    EVENT_TYPE_LABELS = {
        "note": "Note",
        "inspection": "Inspection",
        "maintenance": "Maintenance",
    }
    MATERIAL_ACTIONS = ("replaced", "installed", "repaired", "packed", "removed")
    MATERIAL_ACTION_LABELS = {
        "replaced": "Replaced",
        "installed": "Installed",
        "repaired": "Repaired",
        "packed": "Packed",
        "removed": "Removed",
    }
    MATERIAL_TYPES = ("mesh", "wire", "pole", "dropper", "electric_wire", "stone", "other")
    MATERIAL_TYPE_LABELS = {
        "mesh": "Mesh",
        "wire": "Wire",
        "pole": "Pole",
        "dropper": "Dropper",
        "electric_wire": "Electric Wire",
        "stone": "Stone",
        "other": "Other",
    }
    MATERIAL_UNITS = ("m", "each", "roll", "kg", "bag", "load")
    SUITABILITY_FIELDS = (
        "holds_cattle",
        "holds_sheep",
        "holds_goats",
        "excludes_jackal",
        "excludes_predators",
    )
    MAP_CORE_FIELDS = (
        "name",
        "condition",
        "height_profile",
        "construction_type",
        "electric_wire",
        "electric_wire_type",
        "notes",
        "tags",
    )

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _normalize_key(value: str | None) -> str:
        return " ".join(str(value or "").strip().lower().split()).replace(" ", "_").replace("-", "_")

    @classmethod
    def _choice(cls, raw_value: str | None, allowed: tuple[str, ...], field_label: str, default: str) -> str:
        value = cls._normalize_key(raw_value) or default
        if value not in allowed:
            raise ValueError(f"{field_label} is invalid")
        return value

    @staticmethod
    def _optional_text(value: str | None, max_length: int = 5000) -> str | None:
        return TaskService.optional_text(value, max_length)

    @classmethod
    def _tags_csv(cls, raw_tags: str | list[str] | None) -> str:
        if isinstance(raw_tags, list):
            raw_tags = ",".join(str(value) for value in raw_tags)
        return TaskService.tags_to_csv(TaskService.parse_optional_tags(raw_tags))

    @staticmethod
    def _to_float(value) -> float | None:
        return float(value) if value is not None else None

    @staticmethod
    def _to_bool(value) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _decimal_or_none(value, field_label: str) -> Decimal | None:
        if value in (None, ""):
            return None
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError(f"{field_label} must be a valid number") from exc
        if parsed < 0:
            raise ValueError(f"{field_label} must be greater than or equal to 0")
        return parsed

    @staticmethod
    def _as_geometry_json(lines: list[list[list[float]]]) -> str | None:
        clean_lines = []
        for line in lines:
            clean_line = [[round(float(lon), 8), round(float(lat), 8)] for lon, lat in line]
            if len(clean_line) >= 2 and clean_line[0] != clean_line[-1]:
                clean_lines.append(clean_line)
        if not clean_lines:
            return None
        geometry = (
            {"type": "LineString", "coordinates": clean_lines[0]}
            if len(clean_lines) == 1
            else {"type": "MultiLineString", "coordinates": clean_lines}
        )
        return json.dumps(geometry, separators=(",", ":"))

    @staticmethod
    def _geometry_from_json(value: str | None) -> dict | None:
        if not value:
            return None
        try:
            geometry = json.loads(value)
        except (TypeError, ValueError):
            return None
        if not isinstance(geometry, dict) or geometry.get("type") not in {"LineString", "MultiLineString"}:
            return None
        return geometry

    @staticmethod
    def _geometry_json(geometry: dict) -> str:
        return json.dumps(geometry, separators=(",", ":"))

    @staticmethod
    def _parse_ring(raw: str | None) -> list[tuple[float, float]]:
        points = []
        for token in (raw or "").replace("\n", " ").split():
            parts = [part.strip() for part in token.split(",")]
            if len(parts) < 2:
                continue
            try:
                points.append((float(parts[0]), float(parts[1])))
            except ValueError:
                continue
        if len(points) >= 3 and points[0] != points[-1]:
            points.append(points[0])
        return points if len(points) >= 4 else []

    @staticmethod
    def _open_ring(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if len(points) >= 2 and points[0] == points[-1]:
            return list(points[:-1])
        return list(points)

    @classmethod
    def _segments(cls, ring: list[tuple[float, float]]) -> list[tuple[tuple[float, float], tuple[float, float]]]:
        points = cls._open_ring(ring)
        segments = []
        for index, start in enumerate(points):
            end = points[(index + 1) % len(points)]
            if start != end:
                segments.append((start, end))
        return segments

    @staticmethod
    def _project(point: tuple[float, float], lat0: float) -> tuple[float, float]:
        lon, lat = point
        return (
            EARTH_RADIUS_M * radians(lon) * cos(radians(lat0)),
            EARTH_RADIUS_M * radians(lat),
        )

    @classmethod
    def _segment_overlap(
        cls,
        left: tuple[tuple[float, float], tuple[float, float]],
        right: tuple[tuple[float, float], tuple[float, float]],
    ) -> dict | None:
        left_start, left_end = left
        right_start, right_end = right
        lat0 = (left_start[1] + left_end[1] + right_start[1] + right_end[1]) / 4.0
        ax, ay = cls._project(left_start, lat0)
        bx, by = cls._project(left_end, lat0)
        cx, cy = cls._project(right_start, lat0)
        dx, dy = cls._project(right_end, lat0)
        vx = bx - ax
        vy = by - ay
        length_sq = (vx * vx) + (vy * vy)
        if length_sq <= 0:
            return None

        length = length_sq**0.5
        distances = [
            abs((vx * (cy - ay)) - (vy * (cx - ax))) / length,
            abs((vx * (dy - ay)) - (vy * (dx - ax))) / length,
        ]
        if max(distances) > BOUNDARY_TOLERANCE_M:
            return None

        c_t = (((cx - ax) * vx) + ((cy - ay) * vy)) / length_sq
        d_t = (((dx - ax) * vx) + ((dy - ay) * vy)) / length_sq
        overlap_start = max(0.0, min(c_t, d_t))
        overlap_end = min(1.0, max(c_t, d_t))
        if overlap_end <= overlap_start:
            return None

        overlap_length = length * (overlap_end - overlap_start)
        if overlap_length < MIN_FENCE_SECTION_LENGTH_M:
            return None

        start = (
            left_start[0] + ((left_end[0] - left_start[0]) * overlap_start),
            left_start[1] + ((left_end[1] - left_start[1]) * overlap_start),
        )
        end = (
            left_start[0] + ((left_end[0] - left_start[0]) * overlap_end),
            left_start[1] + ((left_end[1] - left_start[1]) * overlap_end),
        )
        return {"length_m": overlap_length, "line": [[start[0], start[1]], [end[0], end[1]]]}

    @classmethod
    def _line_length_m(cls, line: tuple[tuple[float, float], tuple[float, float]]) -> float:
        start, end = line
        lat0 = (start[1] + end[1]) / 2.0
        ax, ay = cls._project(start, lat0)
        bx, by = cls._project(end, lat0)
        return ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5

    @classmethod
    def _polyline_length_m(cls, points: list[list[float]]) -> float:
        total = 0.0
        for index in range(len(points) - 1):
            start = (points[index][0], points[index][1])
            end = (points[index + 1][0], points[index + 1][1])
            total += cls._line_length_m((start, end))
        return total

    @staticmethod
    def _clean_coordinate(raw_point, field_label: str) -> list[float]:
        if not isinstance(raw_point, (list, tuple)) or len(raw_point) < 2:
            raise ValueError(f"{field_label} coordinate is invalid")
        try:
            lon = float(raw_point[0])
            lat = float(raw_point[1])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_label} coordinate is invalid") from exc
        if not isfinite(lon) or not isfinite(lat):
            raise ValueError(f"{field_label} coordinate is invalid")
        if lon < -180 or lon > 180 or lat < -90 or lat > 90:
            raise ValueError(f"{field_label} coordinate is outside valid longitude/latitude bounds")
        return [round(lon, 8), round(lat, 8)]

    @classmethod
    def normalize_geometry(cls, geometry) -> tuple[dict, float]:
        if not isinstance(geometry, dict):
            raise ValueError("Fence geometry must be GeoJSON")

        geometry_type = geometry.get("type")
        coordinates = geometry.get("coordinates")
        if geometry_type == "LineString":
            raw_lines = [coordinates]
        elif geometry_type == "MultiLineString":
            raw_lines = coordinates
        else:
            raise ValueError("Fence geometry must be a LineString or MultiLineString")

        if not isinstance(raw_lines, list) or not raw_lines:
            raise ValueError("Fence geometry must include at least one line")

        clean_lines = []
        total_length = 0.0
        for line_index, raw_line in enumerate(raw_lines, start=1):
            if not isinstance(raw_line, list) or len(raw_line) < 2:
                raise ValueError("Each fence line must include at least two points")
            clean_line = []
            for raw_point in raw_line:
                point = cls._clean_coordinate(raw_point, f"Fence line {line_index}")
                if not clean_line or clean_line[-1] != point:
                    clean_line.append(point)
            if len(clean_line) < 2 or len({tuple(point) for point in clean_line}) < 2:
                raise ValueError("Each fence line must include at least two distinct points")
            line_length = cls._polyline_length_m(clean_line)
            if line_length < MIN_FENCE_SECTION_LENGTH_M:
                raise ValueError("Each fence line is too short")
            total_length += line_length
            clean_lines.append(clean_line)

        normalized_geometry = (
            {"type": "LineString", "coordinates": clean_lines[0]}
            if len(clean_lines) == 1
            else {"type": "MultiLineString", "coordinates": clean_lines}
        )
        return normalized_geometry, round(total_length, 2)

    @classmethod
    def _resolve_section_paddocks(
        cls,
        farm_id: str,
        payload: dict,
        *,
        section: FenceSection | None = None,
    ) -> tuple[str, Paddock, Paddock | None]:
        default_type = section.section_type if section is not None else FenceSection.TYPE_BOUNDARY
        section_type = cls._choice(
            payload.get("section_type"),
            (FenceSection.TYPE_BOUNDARY, FenceSection.TYPE_INTERNAL),
            "Fence type",
            default_type,
        )
        paddock_a_id = str(payload.get("paddock_a_id") or (section.paddock_a_id if section else "") or "").strip()
        paddock_b_id = str(payload.get("paddock_b_id") or (section.paddock_b_id if section else "") or "").strip()
        if not paddock_a_id:
            raise ValueError("Paddock A is required")
        paddock_a = Paddock.query.filter_by(id=paddock_a_id, farm_id=farm_id).first()
        if paddock_a is None:
            raise ValueError("Paddock A must belong to the fence farm")
        paddock_b = None
        if section_type == FenceSection.TYPE_INTERNAL:
            if not paddock_b_id:
                raise ValueError("Paddock B is required for internal fences")
            if paddock_a_id == paddock_b_id:
                raise ValueError("Internal fences must connect two different paddocks")
            paddock_b = Paddock.query.filter_by(id=paddock_b_id, farm_id=farm_id).first()
            if paddock_b is None:
                raise ValueError("Paddock B must belong to the fence farm")
            paddock_a, paddock_b = sorted([paddock_a, paddock_b], key=lambda row: str(row.id))
        return section_type, paddock_a, paddock_b

    @classmethod
    def _load_kml_rows(cls, farm: Farm, instance_path: str | Path) -> list[dict]:
        kml_path = Path(instance_path) / "maps" / f"{farm.name}.kml"
        if not kml_path.exists():
            raise ValueError(f"Farm map KML file not found: {kml_path}")
        root = ET.parse(kml_path).getroot()
        farm_name_key = PaddockService.normalize_name(farm.name).casefold()
        paddocks_by_name = {
            PaddockService.normalize_name(paddock.name).casefold(): paddock
            for paddock in Paddock.query.filter_by(farm_id=farm.id, status="active").all()
        }
        rows = []
        for placemark in root.findall(".//k:Placemark", KML_NAMESPACES):
            name = (placemark.findtext("k:name", default="", namespaces=KML_NAMESPACES) or "").strip()
            key = PaddockService.normalize_name(name).casefold()
            if not key or key == farm_name_key or key not in paddocks_by_name:
                continue
            rings = []
            for polygon in placemark.findall(".//k:Polygon", KML_NAMESPACES):
                raw = polygon.findtext(
                    ".//k:outerBoundaryIs/k:LinearRing/k:coordinates",
                    default="",
                    namespaces=KML_NAMESPACES,
                )
                ring = cls._parse_ring(raw)
                if ring:
                    rings.append(ring)
            if rings:
                rows.append({"paddock": paddocks_by_name[key], "rings": rings})
        return rows

    @classmethod
    def _parsed_import_rows(cls, parsed_paddocks: list[dict]) -> list[dict]:
        rows = []
        for payload in parsed_paddocks:
            paddock = payload.get("db_paddock")
            if paddock is None:
                continue
            rings = [
                polygon["outer"]
                for polygon in payload.get("geometry", [])
                if polygon.get("outer")
            ]
            if rings:
                rows.append({"paddock": paddock, "rings": rings})
        return rows

    @classmethod
    def detect_sections(cls, rows: list[dict]) -> list[dict]:
        detected = []
        row_segments = []
        for row in rows:
            row_segments.append(
                {
                    "paddock": row["paddock"],
                    "segments": [segment for ring in row["rings"] for segment in cls._segments(ring)],
                }
            )

        shared_by_pair: dict[tuple[str, str], dict] = {}
        boundary_segments_by_paddock: dict[str, list[tuple[tuple[float, float], tuple[float, float]]]] = {
            str(row["paddock"].id): list(row["segments"]) for row in row_segments
        }
        for left_index, left in enumerate(row_segments):
            for right in row_segments[left_index + 1 :]:
                lines = []
                total_length = 0.0
                for left_segment in left["segments"]:
                    for right_segment in right["segments"]:
                        overlap = cls._segment_overlap(left_segment, right_segment)
                        if not overlap:
                            continue
                        total_length += overlap["length_m"]
                        lines.append(overlap["line"])
                        left_id = str(left["paddock"].id)
                        right_id = str(right["paddock"].id)
                        boundary_segments_by_paddock[left_id] = [
                            segment
                            for segment in boundary_segments_by_paddock[left_id]
                            if segment != left_segment
                        ]
                        boundary_segments_by_paddock[right_id] = [
                            segment
                            for segment in boundary_segments_by_paddock[right_id]
                            if segment != right_segment
                        ]
                if total_length >= MIN_FENCE_SECTION_LENGTH_M and lines:
                    paddock_a, paddock_b = sorted([left["paddock"], right["paddock"]], key=lambda item: str(item.id))
                    key = (str(paddock_a.id), str(paddock_b.id))
                    shared_by_pair[key] = {
                        "section_key": f"internal:{key[0]}:{key[1]}",
                        "name": f"{paddock_a.name} / {paddock_b.name} Fence",
                        "section_type": FenceSection.TYPE_INTERNAL,
                        "paddock_a": paddock_a,
                        "paddock_b": paddock_b,
                        "length_m": round(total_length, 2),
                        "lines": lines,
                    }

        detected.extend(shared_by_pair.values())
        paddock_by_id = {str(row["paddock"].id): row["paddock"] for row in row_segments}
        for paddock_id, segments in boundary_segments_by_paddock.items():
            lines = []
            total_length = 0.0
            for segment in segments:
                length = cls._line_length_m(segment)
                if length < MIN_FENCE_SECTION_LENGTH_M:
                    continue
                total_length += length
                lines.append([[segment[0][0], segment[0][1]], [segment[1][0], segment[1][1]]])
            if not lines:
                continue
            paddock = paddock_by_id[paddock_id]
            detected.append(
                {
                    "section_key": f"boundary:{paddock_id}",
                    "name": f"{paddock.name} Boundary Fence",
                    "section_type": FenceSection.TYPE_BOUNDARY,
                    "paddock_a": paddock,
                    "paddock_b": None,
                    "length_m": round(total_length, 2),
                    "lines": lines,
                }
            )
        return detected

    @classmethod
    def sync_auto_sections_from_candidates(cls, farm_id: str, rows: list[dict]) -> dict:
        detections = cls.detect_sections(rows)
        existing = {
            section.section_key: section
            for section in FenceSection.query.filter_by(farm_id=farm_id).all()
        }
        seen_keys = set()
        created = 0
        updated = 0
        retired = 0
        for detection in detections:
            section_key = detection["section_key"]
            seen_keys.add(section_key)
            section = existing.get(section_key)
            if section is None:
                section = FenceSection(
                    farm_id=farm_id,
                    section_key=section_key,
                    name=detection["name"],
                    active=True,
                    source=FenceSection.SOURCE_AUTO,
                    section_type=detection["section_type"],
                    paddock_a_id=detection["paddock_a"].id,
                    paddock_b_id=detection["paddock_b"].id if detection["paddock_b"] else None,
                    condition="unknown",
                    height_profile="low",
                    construction_type=(
                        "mesh" if detection["section_type"] == FenceSection.TYPE_BOUNDARY else "high_strung_wire"
                    ),
                    wire_type=(
                        "unknown" if detection["section_type"] == FenceSection.TYPE_BOUNDARY else "high_strung"
                    ),
                    mesh_type=("wire_mesh" if detection["section_type"] == FenceSection.TYPE_BOUNDARY else "none"),
                )
                db.session.add(section)
                existing[section_key] = section
                created += 1
            elif section.source != FenceSection.SOURCE_AUTO:
                continue
            else:
                updated += 1
            section.name = detection["name"]
            section.active = True
            section.section_type = detection["section_type"]
            section.paddock_a_id = detection["paddock_a"].id
            section.paddock_b_id = detection["paddock_b"].id if detection["paddock_b"] else None
            section.length_m = detection["length_m"]
            section.geometry_json = cls._as_geometry_json(detection["lines"])

        for section_key, section in existing.items():
            if section_key not in seen_keys and section.source == FenceSection.SOURCE_AUTO and section.active:
                section.active = False
                retired += 1

        return {"created": created, "updated": updated, "retired": retired}

    @classmethod
    def sync_auto_sections_for_import(cls, farm_id: str, parsed_paddocks: list[dict]) -> dict:
        return cls.sync_auto_sections_from_candidates(
            farm_id,
            cls._parsed_import_rows(parsed_paddocks),
        )

    @classmethod
    def sync_auto_sections_for_farm(cls, farm: Farm, instance_path: str | Path) -> dict:
        return cls.sync_auto_sections_from_candidates(
            str(farm.id),
            cls._load_kml_rows(farm, instance_path),
        )

    @classmethod
    def sections_for_farm(cls, farm_id: str, *, active_only: bool = True) -> list[FenceSection]:
        query = FenceSection.query.filter_by(farm_id=farm_id)
        if active_only:
            query = query.filter_by(active=True)
        return query.order_by(FenceSection.section_type.asc(), FenceSection.name.asc()).all()

    @classmethod
    def sections_for_paddock(cls, paddock_id: str) -> list[FenceSection]:
        return (
            FenceSection.query.filter(
                FenceSection.active.is_(True),
                (FenceSection.paddock_a_id == paddock_id) | (FenceSection.paddock_b_id == paddock_id),
            )
            .order_by(FenceSection.section_type.asc(), FenceSection.name.asc())
            .all()
        )

    @classmethod
    def update_section(cls, section: FenceSection, payload: dict) -> FenceSection:
        if "name" in payload:
            section.name = TaskService.require_text(payload.get("name"), "Fence name", 200)
        if "active" in payload:
            section.active = cls._to_bool(payload.get("active"))
        if "condition" in payload:
            section.condition = cls._choice(payload.get("condition"), cls.CONDITIONS, "Fence condition", section.condition)
        if "height_profile" in payload:
            section.height_profile = cls._choice(
                payload.get("height_profile"),
                cls.HEIGHT_PROFILES,
                "Fence height",
                section.height_profile,
            )
        if "construction_type" in payload:
            section.construction_type = cls._choice(
                payload.get("construction_type"),
                cls.CONSTRUCTION_TYPES,
                "Fence construction",
                section.construction_type,
            )
        if "post_type" in payload:
            section.post_type = cls._choice(payload.get("post_type"), cls.POST_TYPES, "Post type", section.post_type)
        if "dropper_type" in payload:
            section.dropper_type = cls._choice(
                payload.get("dropper_type"),
                cls.DROPPER_TYPES,
                "Dropper type",
                section.dropper_type,
            )
        if "wire_type" in payload:
            section.wire_type = cls._choice(payload.get("wire_type"), cls.WIRE_TYPES, "Wire type", section.wire_type)
        if "mesh_type" in payload:
            section.mesh_type = cls._choice(payload.get("mesh_type"), cls.MESH_TYPES, "Mesh type", section.mesh_type)
        if "electric_wire" in payload:
            section.electric_wire = cls._to_bool(payload.get("electric_wire"))
        if "electric_wire_type" in payload:
            section.electric_wire_type = cls._optional_text(payload.get("electric_wire_type"), 80)
        if "notes" in payload:
            section.notes = cls._optional_text(payload.get("notes"))
        if "tags" in payload:
            section.tags_csv = cls._tags_csv(payload.get("tags"))
        if "tags_csv" in payload:
            section.tags_csv = cls._tags_csv(payload.get("tags_csv"))
        for field_name in cls.SUITABILITY_FIELDS:
            if field_name in payload:
                section_value = getattr(section, field_name)
                setattr(
                    section,
                    field_name,
                    cls._choice(payload.get(field_name), cls.SUITABILITY_VALUES, field_name.replace("_", " ").title(), section_value),
                )
        return section

    @classmethod
    def update_section_geometry(cls, section: FenceSection, geometry) -> FenceSection:
        normalized_geometry, length_m = cls.normalize_geometry(geometry)
        section.geometry_json = cls._geometry_json(normalized_geometry)
        section.length_m = Decimal(str(length_m))
        section.source = FenceSection.SOURCE_MANUAL
        return section

    @classmethod
    def update_section_from_map(cls, section: FenceSection, payload: dict) -> FenceSection:
        if not isinstance(payload, dict):
            raise ValueError("Fence payload is invalid")

        if any(field in payload for field in ("section_type", "paddock_a_id", "paddock_b_id")):
            section_type, paddock_a, paddock_b = cls._resolve_section_paddocks(
                str(section.farm_id),
                payload,
                section=section,
            )
            section.section_type = section_type
            section.paddock_a_id = paddock_a.id
            section.paddock_b_id = paddock_b.id if paddock_b else None
            section.source = FenceSection.SOURCE_MANUAL

        if "geometry" in payload:
            cls.update_section_geometry(section, payload.get("geometry"))

        cls.update_section(
            section,
            {field: payload[field] for field in cls.MAP_CORE_FIELDS if field in payload},
        )
        return section

    @classmethod
    def create_manual_section(cls, farm_id: str, payload: dict) -> FenceSection:
        if not isinstance(payload, dict):
            raise ValueError("Fence payload is invalid")
        section_type, paddock_a, paddock_b = cls._resolve_section_paddocks(farm_id, payload)
        normalized_geometry, length_m = cls.normalize_geometry(payload.get("geometry"))
        section = FenceSection(
            farm_id=farm_id,
            section_key=f"manual:{uuid.uuid4()}",
            name=TaskService.require_text(payload.get("name"), "Fence name", 200),
            active=True,
            source=FenceSection.SOURCE_MANUAL,
            section_type=section_type,
            paddock_a_id=paddock_a.id,
            paddock_b_id=paddock_b.id if paddock_b else None,
            length_m=Decimal(str(length_m)),
            geometry_json=cls._geometry_json(normalized_geometry),
            condition="unknown",
            height_profile="low",
            construction_type=("mesh" if section_type == FenceSection.TYPE_BOUNDARY else "high_strung_wire"),
            wire_type=("unknown" if section_type == FenceSection.TYPE_BOUNDARY else "high_strung"),
            mesh_type=("wire_mesh" if section_type == FenceSection.TYPE_BOUNDARY else "none"),
        )
        db.session.add(section)
        cls.update_section(
            section,
            {field: payload[field] for field in cls.MAP_CORE_FIELDS if field in payload},
        )
        db.session.flush()
        return section

    @classmethod
    def archive_section(cls, section: FenceSection) -> FenceSection:
        section.active = False
        section.source = FenceSection.SOURCE_MANUAL
        return section

    @classmethod
    def parse_material_rows(cls, rows) -> list[dict]:
        if rows in (None, ""):
            return []
        if not isinstance(rows, list):
            raise ValueError("Material rows must be a list")
        material_rows = []
        for row in rows:
            if row is None:
                continue
            if not isinstance(row, dict):
                raise ValueError("Material row is invalid")
            if not any(str(row.get(name) or "").strip() for name in ("action", "material_type", "material_detail", "quantity", "unit", "notes")):
                continue
            action = cls._choice(row.get("action"), cls.MATERIAL_ACTIONS, "Material action", "")
            material_type = cls._choice(row.get("material_type"), cls.MATERIAL_TYPES, "Material type", "")
            material_detail = cls._optional_text(row.get("material_detail"), 160)
            quantity = cls._decimal_or_none(row.get("quantity"), "Material quantity")
            unit = cls._optional_text(row.get("unit"), 30)
            notes = cls._optional_text(row.get("notes"))
            if unit and unit not in cls.MATERIAL_UNITS:
                raise ValueError("Material unit is invalid")
            if not any([material_detail, quantity is not None, unit, notes]):
                continue
            material_rows.append(
                {
                    "action": action,
                    "material_type": material_type,
                    "material_detail": material_detail,
                    "quantity": quantity,
                    "unit": unit,
                    "notes": notes,
                }
            )
        return material_rows

    @classmethod
    def create_event(
        cls,
        *,
        section: FenceSection,
        event_type: str | None,
        description: str | None,
        raw_tags: str | list[str] | None = None,
        condition_after: str | None = None,
        event_at: datetime | None = None,
        material_rows=None,
    ) -> FenceEvent:
        normalized_description = TaskService.require_text(
            description,
            "Description",
            TaskService.MAX_DESCRIPTION_LENGTH,
        )
        normalized_type = cls._choice(event_type, cls.EVENT_TYPES, "Fence event type", "note")
        normalized_condition = None
        if condition_after not in (None, ""):
            normalized_condition = cls._choice(condition_after, cls.CONDITIONS, "Condition after", "unknown")
            section.condition = normalized_condition
        if isinstance(raw_tags, list):
            raw_tags = ",".join(str(value) for value in raw_tags)
        tags = MobEventService.parse_tags(raw_tags or normalized_type)
        event = FenceEvent(
            fence_section_id=section.id,
            farm_id=section.farm_id,
            event_at=event_at or cls._utcnow(),
            event_type=normalized_type,
            tags_csv=MobEventService.tags_to_csv(tags),
            condition_after=normalized_condition,
            description=normalized_description,
        )
        db.session.add(event)
        db.session.flush()
        for row in cls.parse_material_rows(material_rows):
            db.session.add(FenceEventMaterial(event_id=event.id, **row))
        return event

    @classmethod
    def tags_from_csv(cls, raw_csv: str | None) -> list[str]:
        return MobEventService.tags_from_csv(raw_csv)

    @classmethod
    def serialize_material(cls, material: FenceEventMaterial) -> dict:
        return {
            "id": str(material.id),
            "action": material.action,
            "action_label": cls.MATERIAL_ACTION_LABELS.get(material.action, material.action),
            "material_type": material.material_type,
            "material_type_label": cls.MATERIAL_TYPE_LABELS.get(material.material_type, material.material_type),
            "material_detail": material.material_detail,
            "quantity": cls._to_float(material.quantity),
            "unit": material.unit,
            "notes": material.notes,
        }

    @classmethod
    def serialize_event(cls, event: FenceEvent, *, include_materials: bool = True, include_attachments: bool = False) -> dict:
        payload = {
            "id": str(event.id),
            "farm_id": str(event.farm_id),
            "fence_section_id": str(event.fence_section_id),
            "event_at": event.event_at.isoformat() if event.event_at else None,
            "event_type": event.event_type,
            "event_type_label": cls.EVENT_TYPE_LABELS.get(event.event_type, event.event_type),
            "tags": cls.tags_from_csv(event.tags_csv),
            "condition_after": event.condition_after,
            "condition_after_label": cls.CONDITION_LABELS.get(event.condition_after, event.condition_after),
            "description": event.description,
            "updated_at": event.updated_at.isoformat() if event.updated_at else None,
        }
        if include_materials:
            payload["materials"] = [cls.serialize_material(material) for material in event.materials]
        if include_attachments:
            payload["attachment_count"] = len(event.attachments)
        return payload

    @classmethod
    def serialize_section(cls, section: FenceSection, *, include_events: bool = False) -> dict:
        paddock_names = [
            paddock.name
            for paddock in (section.paddock_a, section.paddock_b)
            if paddock is not None
        ]
        payload = {
            "id": str(section.id),
            "fence_section_id": str(section.id),
            "farm_id": str(section.farm_id),
            "section_key": section.section_key,
            "name": section.name,
            "active": bool(section.active),
            "source": section.source,
            "section_type": section.section_type,
            "section_type_label": section.section_type.replace("_", " ").title(),
            "paddock_a_id": str(section.paddock_a_id),
            "paddock_a_name": section.paddock_a.name if section.paddock_a else None,
            "paddock_b_id": str(section.paddock_b_id) if section.paddock_b_id else None,
            "paddock_b_name": section.paddock_b.name if section.paddock_b else None,
            "paddock_names": paddock_names,
            "length_m": cls._to_float(section.length_m),
            "geometry": cls._geometry_from_json(section.geometry_json),
            "condition": section.condition,
            "condition_label": cls.CONDITION_LABELS.get(section.condition, section.condition),
            "height_profile": section.height_profile,
            "height_profile_label": cls.HEIGHT_LABELS.get(section.height_profile, section.height_profile),
            "construction_type": section.construction_type,
            "construction_type_label": cls.CONSTRUCTION_LABELS.get(section.construction_type, section.construction_type),
            "post_type": section.post_type,
            "dropper_type": section.dropper_type,
            "wire_type": section.wire_type,
            "mesh_type": section.mesh_type,
            "electric_wire": bool(section.electric_wire),
            "electric_wire_type": section.electric_wire_type,
            "notes": section.notes,
            "tags": TaskService.tags_from_csv(section.tags_csv),
            "tags_csv": section.tags_csv,
            "holds_cattle": section.holds_cattle,
            "holds_sheep": section.holds_sheep,
            "holds_goats": section.holds_goats,
            "excludes_jackal": section.excludes_jackal,
            "excludes_predators": section.excludes_predators,
            "updated_at": section.updated_at.isoformat() if section.updated_at else None,
        }
        if include_events:
            payload["events"] = [
                cls.serialize_event(event)
                for event in sorted(section.events, key=lambda row: row.event_at or row.created_at, reverse=True)
            ]
        return payload

    @classmethod
    def map_feature(cls, section: FenceSection) -> dict | None:
        if not section.active:
            return None
        geometry = cls._geometry_from_json(section.geometry_json)
        if not geometry:
            return None
        return {
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                **cls.serialize_section(section),
                "feature_type": "fence_section",
            },
        }

    @classmethod
    def active_map_features_for_farm(cls, farm_id: str) -> list[dict]:
        features = []
        for section in cls.sections_for_farm(farm_id):
            feature = cls.map_feature(section)
            if feature:
                features.append(feature)
        return features

    @classmethod
    def template_options(cls) -> dict:
        def rows(values, labels=None):
            labels = labels or {}
            return [{"value": value, "label": labels.get(value, value.replace("_", " ").title())} for value in values]

        return {
            "conditions": rows(cls.CONDITIONS, cls.CONDITION_LABELS),
            "height_profiles": rows(cls.HEIGHT_PROFILES, cls.HEIGHT_LABELS),
            "construction_types": rows(cls.CONSTRUCTION_TYPES, cls.CONSTRUCTION_LABELS),
            "post_types": rows(cls.POST_TYPES),
            "dropper_types": rows(cls.DROPPER_TYPES),
            "wire_types": rows(cls.WIRE_TYPES),
            "mesh_types": rows(cls.MESH_TYPES),
            "suitability_values": rows(cls.SUITABILITY_VALUES, cls.SUITABILITY_LABELS),
            "event_types": rows(cls.EVENT_TYPES, cls.EVENT_TYPE_LABELS),
            "material_actions": rows(cls.MATERIAL_ACTIONS, cls.MATERIAL_ACTION_LABELS),
            "material_types": rows(cls.MATERIAL_TYPES, cls.MATERIAL_TYPE_LABELS),
            "material_units": rows(cls.MATERIAL_UNITS),
        }
