from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from math import cos, hypot, radians
from pathlib import Path
import xml.etree.ElementTree as ET

from sqlalchemy import or_

from app.extensions import db
from app.models import Farm, GrazingSession, Mob, Paddock, PaddockGate
from app.services.movement_service import MovementService
from app.services.paddock_service import PaddockService

KML_NAMESPACE = "http://www.opengis.net/kml/2.2"
KML_NAMESPACES = {"k": KML_NAMESPACE}
EARTH_RADIUS_M = 6371008.8
FOUR_DECIMAL_PLACES = Decimal("0.0001")
EIGHT_DECIMAL_PLACES = Decimal("0.00000001")
BOUNDARY_TOLERANCE_M = 1.0
MIN_SHARED_BOUNDARY_LENGTH_M = 0.5


class GateService:
    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _normalize_datetime(value: datetime | None) -> datetime:
        if value is None:
            return GateService._utcnow()
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _to_float(value) -> float | None:
        return float(value) if value is not None else None

    @staticmethod
    def _sorted_pair(left_id: str, right_id: str) -> tuple[str, str]:
        left = str(left_id or "").strip()
        right = str(right_id or "").strip()
        if not left or not right:
            raise ValueError("Both paddocks are required")
        if left == right:
            raise ValueError("A gate must connect two different paddocks")
        return tuple(sorted((left, right)))

    @classmethod
    def gates_for_farm(cls, farm_id: str, *, active_only: bool = True) -> list[PaddockGate]:
        query = PaddockGate.query.filter_by(farm_id=farm_id)
        if active_only:
            query = query.filter_by(active=True)
        return (
            query.join(Paddock, PaddockGate.paddock_a_id == Paddock.id)
            .order_by(Paddock.name.asc(), PaddockGate.created_at.asc())
            .all()
        )

    @classmethod
    def adjacent_gates_for_paddock(cls, paddock_id: str) -> list[PaddockGate]:
        return (
            PaddockGate.query.filter(
                PaddockGate.active.is_(True),
                or_(
                    PaddockGate.paddock_a_id == paddock_id,
                    PaddockGate.paddock_b_id == paddock_id,
                ),
            )
            .order_by(PaddockGate.status.asc(), PaddockGate.created_at.asc())
            .all()
        )

    @classmethod
    def validate_gate_status(cls, value: str | None) -> str:
        status = str(value or "").strip().lower()
        if status not in {PaddockGate.STATUS_OPEN, PaddockGate.STATUS_CLOSED}:
            raise ValueError("Gate status must be open or closed")
        return status

    @staticmethod
    def _coordinate_decimal(value, *, label: str, minimum: Decimal, maximum: Decimal) -> Decimal:
        try:
            coordinate = Decimal(str(value).strip()).quantize(EIGHT_DECIMAL_PLACES)
        except (InvalidOperation, ValueError):
            raise ValueError(f"{label} must be a valid number") from None
        if coordinate < minimum or coordinate > maximum:
            raise ValueError(f"{label} must be between {minimum} and {maximum}")
        return coordinate

    @classmethod
    def _optional_coordinate_decimal(cls, value, *, label: str, minimum: Decimal, maximum: Decimal) -> Decimal | None:
        if value in (None, ""):
            return None
        return cls._coordinate_decimal(value, label=label, minimum=minimum, maximum=maximum)

    @classmethod
    def update_gate_location(cls, gate: PaddockGate, *, latitude, longitude) -> PaddockGate:
        if not gate.active:
            raise ValueError("Gate is inactive")
        gate.latitude = cls._coordinate_decimal(
            latitude,
            label="Latitude",
            minimum=Decimal("-90"),
            maximum=Decimal("90"),
        )
        gate.longitude = cls._coordinate_decimal(
            longitude,
            label="Longitude",
            minimum=Decimal("-180"),
            maximum=Decimal("180"),
        )
        gate.source = PaddockGate.SOURCE_MANUAL
        return gate

    @classmethod
    def update_gate_details(
        cls,
        gate: PaddockGate,
        *,
        paddock_a_id: str,
        paddock_b_id: str,
        latitude=None,
        longitude=None,
    ) -> PaddockGate:
        if not gate.active:
            raise ValueError("Gate is inactive")
        next_a_id, next_b_id = cls._sorted_pair(paddock_a_id, paddock_b_id)
        if (next_a_id, next_b_id) != (str(gate.paddock_a_id), str(gate.paddock_b_id)):
            if gate.status == PaddockGate.STATUS_OPEN:
                raise ValueError("Close the gate before changing its connected camps")
            paddocks = {
                str(paddock.id): paddock
                for paddock in Paddock.query.filter(
                    Paddock.farm_id == gate.farm_id,
                    Paddock.id.in_([next_a_id, next_b_id]),
                ).all()
            }
            if set(paddocks) != {next_a_id, next_b_id}:
                raise ValueError("Selected paddocks are invalid for this farm")
            duplicate = PaddockGate.query.filter_by(
                farm_id=gate.farm_id,
                paddock_a_id=next_a_id,
                paddock_b_id=next_b_id,
            ).first()
            if duplicate is not None and str(duplicate.id) != str(gate.id):
                raise ValueError("A gate already exists between those camps")
            gate.paddock_a_id = next_a_id
            gate.paddock_b_id = next_b_id
            gate.shared_boundary_length_m = None

        gate.latitude = cls._optional_coordinate_decimal(
            latitude,
            label="Latitude",
            minimum=Decimal("-90"),
            maximum=Decimal("90"),
        )
        gate.longitude = cls._optional_coordinate_decimal(
            longitude,
            label="Longitude",
            minimum=Decimal("-180"),
            maximum=Decimal("180"),
        )
        gate.source = PaddockGate.SOURCE_MANUAL
        return gate

    @classmethod
    def delete_gate(cls, gate: PaddockGate) -> None:
        if gate.status == PaddockGate.STATUS_OPEN:
            raise ValueError("Close the gate before deleting it")
        db.session.delete(gate)

    @classmethod
    def create_manual_gate(
        cls,
        *,
        farm_id: str,
        paddock_a_id: str,
        paddock_b_id: str,
        latitude=None,
        longitude=None,
    ) -> PaddockGate:
        paddock_a_id, paddock_b_id = cls._sorted_pair(paddock_a_id, paddock_b_id)
        paddocks = {
            str(paddock.id): paddock
            for paddock in Paddock.query.filter(
                Paddock.farm_id == farm_id,
                Paddock.id.in_([paddock_a_id, paddock_b_id]),
            ).all()
        }
        if set(paddocks) != {paddock_a_id, paddock_b_id}:
            raise ValueError("Selected paddocks are invalid for this farm")

        gate = PaddockGate.query.filter_by(
            farm_id=farm_id,
            paddock_a_id=paddock_a_id,
            paddock_b_id=paddock_b_id,
        ).first()
        if gate is None:
            gate = PaddockGate(
                farm_id=farm_id,
                paddock_a_id=paddock_a_id,
                paddock_b_id=paddock_b_id,
                status=PaddockGate.STATUS_CLOSED,
                active=True,
                source=PaddockGate.SOURCE_MANUAL,
                latitude=latitude,
                longitude=longitude,
                last_state_changed_at=cls._utcnow(),
            )
            db.session.add(gate)
        else:
            gate.active = True
            gate.source = PaddockGate.SOURCE_MANUAL
            if latitude not in (None, ""):
                gate.latitude = latitude
            if longitude not in (None, ""):
                gate.longitude = longitude
        return gate

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
    def _load_kml_gate_candidates(cls, farm: Farm, instance_path: str | Path) -> list[dict]:
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
    def _parsed_import_gate_candidates(cls, parsed_paddocks: list[dict]) -> list[dict]:
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

    @staticmethod
    def _project(point: tuple[float, float], lat0: float) -> tuple[float, float]:
        lon, lat = point
        return (
            EARTH_RADIUS_M * radians(lon) * cos(radians(lat0)),
            EARTH_RADIUS_M * radians(lat),
        )

    @staticmethod
    def _segments(ring: list[tuple[float, float]]) -> list[tuple[tuple[float, float], tuple[float, float]]]:
        points = GateService._open_ring(ring)
        segments = []
        for index, start in enumerate(points):
            end = points[(index + 1) % len(points)]
            if start != end:
                segments.append((start, end))
        return segments

    @classmethod
    def _segment_overlap(
        cls,
        left: tuple[tuple[float, float], tuple[float, float]],
        right: tuple[tuple[float, float], tuple[float, float]],
    ) -> tuple[float, tuple[float, float] | None]:
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
            return 0.0, None

        length = length_sq**0.5
        right_distances = [
            abs((vx * (cy - ay)) - (vy * (cx - ax))) / length,
            abs((vx * (dy - ay)) - (vy * (dx - ax))) / length,
        ]
        if max(right_distances) > BOUNDARY_TOLERANCE_M:
            return 0.0, None

        c_t = (((cx - ax) * vx) + ((cy - ay) * vy)) / length_sq
        d_t = (((dx - ax) * vx) + ((dy - ay) * vy)) / length_sq
        overlap_start = max(0.0, min(c_t, d_t))
        overlap_end = min(1.0, max(c_t, d_t))
        if overlap_end <= overlap_start:
            return 0.0, None

        overlap_length = length * (overlap_end - overlap_start)
        if overlap_length < MIN_SHARED_BOUNDARY_LENGTH_M:
            return 0.0, None

        midpoint_t = (overlap_start + overlap_end) / 2.0
        midpoint = (
            left_start[0] + ((left_end[0] - left_start[0]) * midpoint_t),
            left_start[1] + ((left_end[1] - left_start[1]) * midpoint_t),
        )
        return overlap_length, midpoint

    @classmethod
    def detect_shared_boundaries(cls, rows: list[dict]) -> list[dict]:
        detections = []
        for left_index, left in enumerate(rows):
            left_segments = [segment for ring in left["rings"] for segment in cls._segments(ring)]
            for right in rows[left_index + 1 :]:
                right_segments = [segment for ring in right["rings"] for segment in cls._segments(ring)]
                total_length = 0.0
                weighted_lon = 0.0
                weighted_lat = 0.0
                for left_segment in left_segments:
                    for right_segment in right_segments:
                        length, midpoint = cls._segment_overlap(left_segment, right_segment)
                        if not midpoint:
                            continue
                        total_length += length
                        weighted_lon += midpoint[0] * length
                        weighted_lat += midpoint[1] * length
                if total_length >= MIN_SHARED_BOUNDARY_LENGTH_M:
                    detections.append(
                        {
                            "paddock_a": left["paddock"],
                            "paddock_b": right["paddock"],
                            "longitude": weighted_lon / total_length,
                            "latitude": weighted_lat / total_length,
                            "shared_boundary_length_m": total_length,
                        }
                    )
        return detections

    @classmethod
    def sync_auto_gates_from_candidates(cls, farm_id: str, rows: list[dict]) -> dict:
        detections = cls.detect_shared_boundaries(rows)
        existing = {
            (str(gate.paddock_a_id), str(gate.paddock_b_id)): gate
            for gate in PaddockGate.query.filter_by(farm_id=farm_id).all()
        }
        seen_pairs = set()
        created = 0
        updated = 0
        retired = 0
        for detection in detections:
            paddock_a_id, paddock_b_id = cls._sorted_pair(
                detection["paddock_a"].id,
                detection["paddock_b"].id,
            )
            pair = (paddock_a_id, paddock_b_id)
            seen_pairs.add(pair)
            gate = existing.get(pair)
            if gate is None:
                gate = PaddockGate(
                    farm_id=farm_id,
                    paddock_a_id=paddock_a_id,
                    paddock_b_id=paddock_b_id,
                    status=PaddockGate.STATUS_CLOSED,
                    active=True,
                    source=PaddockGate.SOURCE_AUTO,
                    last_state_changed_at=cls._utcnow(),
                )
                db.session.add(gate)
                existing[pair] = gate
                created += 1
            else:
                if gate.source == PaddockGate.SOURCE_AUTO:
                    updated += 1
            gate.active = True
            if gate.source == PaddockGate.SOURCE_AUTO:
                gate.latitude = detection["latitude"]
                gate.longitude = detection["longitude"]
            gate.shared_boundary_length_m = round(detection["shared_boundary_length_m"], 2)

        for pair, gate in existing.items():
            if pair not in seen_pairs and gate.source == PaddockGate.SOURCE_AUTO and gate.active:
                gate.active = False
                retired += 1
        return {"created": created, "updated": updated, "retired": retired}

    @classmethod
    def sync_auto_gates_for_import(cls, farm_id: str, parsed_paddocks: list[dict]) -> dict:
        return cls.sync_auto_gates_from_candidates(
            farm_id,
            cls._parsed_import_gate_candidates(parsed_paddocks),
        )

    @classmethod
    def sync_auto_gates_for_farm(cls, farm: Farm, instance_path: str | Path) -> dict:
        return cls.sync_auto_gates_from_candidates(
            str(farm.id),
            cls._load_kml_gate_candidates(farm, instance_path),
        )

    @classmethod
    def _active_paddocks_by_id(cls, farm_id: str) -> dict[str, Paddock]:
        return {
            str(paddock.id): paddock
            for paddock in Paddock.query.filter_by(farm_id=farm_id, status="active").all()
        }

    @classmethod
    def _open_edges(
        cls,
        farm_id: str,
        *,
        override_gate: PaddockGate | None = None,
        override_status: str | None = None,
    ) -> list[tuple[str, str]]:
        edges = []
        for gate in PaddockGate.query.filter_by(farm_id=farm_id, active=True).all():
            status = gate.status
            if override_gate is not None and str(gate.id) == str(override_gate.id):
                status = override_status or status
            if status == PaddockGate.STATUS_OPEN:
                edges.append((str(gate.paddock_a_id), str(gate.paddock_b_id)))
        if override_gate is not None and override_gate.id is None and override_status == PaddockGate.STATUS_OPEN:
            edges.append((str(override_gate.paddock_a_id), str(override_gate.paddock_b_id)))
        return edges

    @staticmethod
    def _components(paddock_ids: set[str], edges: list[tuple[str, str]]) -> list[set[str]]:
        adjacency = {paddock_id: set() for paddock_id in paddock_ids}
        for left, right in edges:
            if left in adjacency and right in adjacency:
                adjacency[left].add(right)
                adjacency[right].add(left)
        components = []
        seen = set()
        for paddock_id in sorted(paddock_ids):
            if paddock_id in seen:
                continue
            stack = [paddock_id]
            component = set()
            seen.add(paddock_id)
            while stack:
                current = stack.pop()
                component.add(current)
                for neighbor in adjacency[current]:
                    if neighbor not in seen:
                        seen.add(neighbor)
                        stack.append(neighbor)
            components.append(component)
        return components

    @classmethod
    def _component_containing(cls, components: list[set[str]], paddock_id: str) -> set[str]:
        for component in components:
            if paddock_id in component:
                return component
        return {paddock_id}

    @classmethod
    def _component_options(cls, components: list[set[str]], paddocks_by_id: dict[str, Paddock]) -> list[dict]:
        options = []
        for component in components:
            paddocks = sorted(
                [paddocks_by_id[paddock_id] for paddock_id in component if paddock_id in paddocks_by_id],
                key=lambda paddock: paddock.name.lower(),
            )
            if not paddocks:
                continue
            options.append(
                {
                    "component_paddock_id": str(paddocks[0].id),
                    "paddock_ids": [str(paddock.id) for paddock in paddocks],
                    "paddock_names": [paddock.name for paddock in paddocks],
                    "label": ", ".join(paddock.name for paddock in paddocks),
                }
            )
        return options

    @classmethod
    def close_requirements(cls, gate: PaddockGate) -> dict:
        if gate.status != PaddockGate.STATUS_OPEN:
            return {"requires_choices": False, "mobs": [], "components": []}

        farm_id = str(gate.farm_id)
        paddocks_by_id = cls._active_paddocks_by_id(farm_id)
        all_ids = set(paddocks_by_id)
        before_component = cls._component_containing(
            cls._components(all_ids, cls._open_edges(farm_id)),
            str(gate.paddock_a_id),
        )
        after_components = [
            component
            for component in cls._components(
                before_component,
                cls._open_edges(
                    farm_id,
                    override_gate=gate,
                    override_status=PaddockGate.STATUS_CLOSED,
                ),
            )
            if component
        ]
        if len(after_components) <= 1:
            return {"requires_choices": False, "mobs": [], "components": []}

        component_by_paddock = {
            paddock_id: index
            for index, component in enumerate(after_components)
            for paddock_id in component
        }
        requirements = []
        for session in cls._active_sessions(farm_id):
            component_indexes = {
                component_by_paddock[str(allocation.paddock_id)]
                for allocation in session.allocations
                if str(allocation.paddock_id) in component_by_paddock
                and Decimal(str(allocation.allocation_fraction)) > 0
            }
            if len(component_indexes) > 1:
                requirements.append(
                    {
                        "mob_id": str(session.mob_id),
                        "mob_name": session.mob.name,
                    }
                )

        return {
            "requires_choices": bool(requirements),
            "mobs": requirements,
            "components": cls._component_options(after_components, paddocks_by_id),
        }

    @classmethod
    def _active_sessions(cls, farm_id: str) -> list[GrazingSession]:
        return (
            GrazingSession.query.join(Mob)
            .filter(
                GrazingSession.farm_id == farm_id,
                GrazingSession.end_at.is_(None),
                Mob.status == "active",
            )
            .order_by(GrazingSession.start_at.asc())
            .all()
        )

    @staticmethod
    def _effective_area(paddock: Paddock) -> float:
        grazeable = float(paddock.grazeable_area_ha or 0)
        if grazeable > 0:
            return grazeable
        return max(0.0, float(paddock.area_ha or 0))

    @staticmethod
    def _decimal_units(value: Decimal, quantum: Decimal) -> int:
        return int((value / quantum).to_integral_value())

    @classmethod
    def _split_decimal_value(
        cls,
        total_value: Decimal,
        ratios: list[float],
        *,
        quantum: Decimal,
    ) -> list[Decimal]:
        if total_value <= 0 or not ratios:
            return [Decimal("0")] * len(ratios)
        positive_ratios = [max(0.0, float(ratio)) for ratio in ratios]
        positive_total = sum(positive_ratios)
        if positive_total <= 0:
            positive_ratios = [1.0 for _ in ratios]
            positive_total = float(len(ratios))

        total_units = cls._decimal_units(total_value, quantum)
        if total_units <= 0:
            return [Decimal("0")] * len(ratios)

        normalized = [Decimal(str(ratio / positive_total)) for ratio in positive_ratios]
        base_units = []
        remainders = []
        assigned_units = 0
        for index, ratio in enumerate(normalized):
            raw_units = Decimal(total_units) * ratio
            units = int(raw_units.to_integral_value(rounding=ROUND_DOWN))
            base_units.append(units)
            assigned_units += units
            remainders.append((raw_units - Decimal(units), index))

        remaining_units = total_units - assigned_units
        for _, index in sorted(remainders, key=lambda item: (-item[0], item[1])):
            if remaining_units <= 0:
                break
            if positive_ratios[index] <= 0:
                continue
            base_units[index] += 1
            remaining_units -= 1
        return [Decimal(units) * quantum for units in base_units]

    @classmethod
    def _area_weighted_allocations(
        cls,
        paddocks: list[Paddock],
        total_fraction: Decimal,
    ) -> dict[str, Decimal]:
        sorted_paddocks = sorted(paddocks, key=lambda paddock: paddock.name.lower())
        ratios = [cls._effective_area(paddock) for paddock in sorted_paddocks]
        splits = cls._split_decimal_value(total_fraction, ratios, quantum=FOUR_DECIMAL_PLACES)
        return {
            str(paddock.id): split
            for paddock, split in zip(sorted_paddocks, splits)
            if split > 0
        }

    @staticmethod
    def _allocation_map(session: GrazingSession) -> dict[str, Decimal]:
        return {
            str(allocation.paddock_id): Decimal(str(allocation.allocation_fraction))
            for allocation in session.allocations
        }

    @classmethod
    def _allocation_payloads(
        cls,
        allocations: dict[str, Decimal],
        paddocks_by_id: dict[str, Paddock],
    ) -> list[dict]:
        positive = {
            paddock_id: value
            for paddock_id, value in allocations.items()
            if value > 0 and paddock_id in paddocks_by_id
        }
        total = sum(positive.values(), Decimal("0"))
        if total != Decimal("1") and positive:
            first_key = sorted(positive, key=lambda key: paddocks_by_id[key].name.lower())[0]
            positive[first_key] = positive[first_key] + (Decimal("1") - total)
        return [
            {"paddock_id": paddock_id, "allocation_fraction": str(positive[paddock_id])}
            for paddock_id in sorted(positive, key=lambda key: paddocks_by_id[key].name.lower())
            if positive[paddock_id] > 0
        ]

    @classmethod
    def _apply_allocations(
        cls,
        *,
        farm_id: str,
        sessions: list[GrazingSession],
        target_maps: dict[str, dict[str, Decimal]],
        paddocks_by_id: dict[str, Paddock],
        event_time: datetime,
    ) -> int:
        moved = 0
        for session in sessions:
            mob_id = str(session.mob_id)
            target_map = target_maps.get(mob_id)
            if target_map is None:
                continue
            if cls._allocation_map(session) == target_map:
                continue
            payloads = cls._allocation_payloads(target_map, paddocks_by_id)
            if not payloads:
                continue
            MovementService.move_mob(
                mob=session.mob,
                allocations=payloads,
                destination_farm_id=farm_id,
                when=event_time,
            )
            moved += 1
        return moved

    @classmethod
    def _choice_map(cls, closure_choices: list[dict] | None) -> dict[str, str]:
        choices = {}
        for row in closure_choices or []:
            mob_id = str(row.get("mob_id") or "").strip()
            component_paddock_id = str(row.get("component_paddock_id") or "").strip()
            if mob_id and component_paddock_id:
                choices[mob_id] = component_paddock_id
        return choices

    @classmethod
    def _redistribute_for_open(
        cls,
        gate: PaddockGate,
        paddocks_by_id: dict[str, Paddock],
        event_time: datetime,
    ) -> int:
        farm_id = str(gate.farm_id)
        all_ids = set(paddocks_by_id)
        component = cls._component_containing(
            cls._components(
                all_ids,
                cls._open_edges(
                    farm_id,
                    override_gate=gate,
                    override_status=PaddockGate.STATUS_OPEN,
                ),
            ),
            str(gate.paddock_a_id),
        )
        component_paddocks = [paddocks_by_id[paddock_id] for paddock_id in component]
        sessions = cls._active_sessions(farm_id)
        target_maps = {}
        for session in sessions:
            current = cls._allocation_map(session)
            total_in_component = sum(
                (fraction for paddock_id, fraction in current.items() if paddock_id in component),
                Decimal("0"),
            )
            if total_in_component <= 0:
                continue
            target = {
                paddock_id: fraction
                for paddock_id, fraction in current.items()
                if paddock_id not in component
            }
            target.update(cls._area_weighted_allocations(component_paddocks, total_in_component))
            target_maps[str(session.mob_id)] = target
        return cls._apply_allocations(
            farm_id=farm_id,
            sessions=sessions,
            target_maps=target_maps,
            paddocks_by_id=paddocks_by_id,
            event_time=event_time,
        )

    @classmethod
    def _redistribute_for_close(
        cls,
        gate: PaddockGate,
        paddocks_by_id: dict[str, Paddock],
        closure_choices: list[dict] | None,
        event_time: datetime,
    ) -> int:
        farm_id = str(gate.farm_id)
        all_ids = set(paddocks_by_id)
        before_component = cls._component_containing(
            cls._components(all_ids, cls._open_edges(farm_id)),
            str(gate.paddock_a_id),
        )
        after_components = cls._components(
            before_component,
            cls._open_edges(
                farm_id,
                override_gate=gate,
                override_status=PaddockGate.STATUS_CLOSED,
            ),
        )
        if len(after_components) <= 1:
            return 0

        component_by_paddock = {
            paddock_id: index
            for index, component in enumerate(after_components)
            for paddock_id in component
        }
        choices = cls._choice_map(closure_choices)
        sessions = cls._active_sessions(farm_id)
        target_maps = {}
        missing_choices = []
        for session in sessions:
            current = cls._allocation_map(session)
            total_in_previous = sum(
                (
                    fraction
                    for paddock_id, fraction in current.items()
                    if paddock_id in before_component
                ),
                Decimal("0"),
            )
            if total_in_previous <= 0:
                continue

            component_indexes = {
                component_by_paddock[paddock_id]
                for paddock_id, fraction in current.items()
                if paddock_id in component_by_paddock and fraction > 0
            }
            if len(component_indexes) > 1:
                selected_paddock_id = choices.get(str(session.mob_id))
                if selected_paddock_id not in component_by_paddock:
                    missing_choices.append(session.mob.name)
                    continue
                target_component = after_components[component_by_paddock[selected_paddock_id]]
            elif component_indexes:
                target_component = after_components[next(iter(component_indexes))]
            else:
                target_component = after_components[0]

            target = {
                paddock_id: fraction
                for paddock_id, fraction in current.items()
                if paddock_id not in before_component
            }
            target.update(
                cls._area_weighted_allocations(
                    [paddocks_by_id[paddock_id] for paddock_id in target_component],
                    total_in_previous,
                )
            )
            target_maps[str(session.mob_id)] = target

        if missing_choices:
            names = ", ".join(sorted(missing_choices))
            raise ValueError(f"Choose a closing-side camp for: {names}")

        return cls._apply_allocations(
            farm_id=farm_id,
            sessions=sessions,
            target_maps=target_maps,
            paddocks_by_id=paddocks_by_id,
            event_time=event_time,
        )

    @classmethod
    def set_gate_state(
        cls,
        gate: PaddockGate,
        status: str,
        *,
        event_time: datetime | None = None,
        closure_choices: list[dict] | None = None,
    ) -> dict:
        target_status = cls.validate_gate_status(status)
        event_time = cls._normalize_datetime(event_time)
        if not gate.active:
            raise ValueError("Gate is inactive")
        if gate.status == target_status:
            return {"gate": gate, "moved_mob_count": 0}

        farm_id = str(gate.farm_id)
        paddocks_by_id = cls._active_paddocks_by_id(farm_id)
        if str(gate.paddock_a_id) not in paddocks_by_id or str(gate.paddock_b_id) not in paddocks_by_id:
            raise ValueError("Gate paddocks must be active")

        if target_status == PaddockGate.STATUS_OPEN:
            moved_count = cls._redistribute_for_open(gate, paddocks_by_id, event_time)
        else:
            moved_count = cls._redistribute_for_close(
                gate,
                paddocks_by_id,
                closure_choices,
                event_time,
            )

        gate.status = target_status
        gate.last_state_changed_at = event_time
        return {"gate": gate, "moved_mob_count": moved_count}

    @classmethod
    def serialize_gate(cls, gate: PaddockGate) -> dict:
        paddock_a_name = gate.paddock_a.name if gate.paddock_a else None
        paddock_b_name = gate.paddock_b.name if gate.paddock_b else None
        return {
            "id": str(gate.id),
            "gate_id": str(gate.id),
            "farm_id": str(gate.farm_id),
            "paddock_a_id": str(gate.paddock_a_id),
            "paddock_a_name": paddock_a_name,
            "paddock_b_id": str(gate.paddock_b_id),
            "paddock_b_name": paddock_b_name,
            "paddock_names": [name for name in [paddock_a_name, paddock_b_name] if name],
            "name": f"{paddock_a_name or 'Paddock'} / {paddock_b_name or 'Paddock'} Gate",
            "status": gate.status,
            "active": bool(gate.active),
            "source": gate.source,
            "latitude": cls._to_float(gate.latitude),
            "longitude": cls._to_float(gate.longitude),
            "shared_boundary_length_m": cls._to_float(gate.shared_boundary_length_m),
            "last_state_changed_at": gate.last_state_changed_at.isoformat()
            if gate.last_state_changed_at
            else None,
            "updated_at": gate.updated_at.isoformat() if gate.updated_at else None,
        }

    @classmethod
    def gate_map_feature(cls, gate: PaddockGate) -> dict | None:
        if not gate.active or gate.latitude is None or gate.longitude is None:
            return None
        properties = cls.serialize_gate(gate)
        properties["feature_type"] = "gate"
        return {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [float(gate.longitude), float(gate.latitude)],
            },
            "properties": properties,
        }

    @classmethod
    def active_map_features_for_farm(cls, farm_id: str) -> list[dict]:
        features = []
        for gate in cls.gates_for_farm(farm_id):
            feature = cls.gate_map_feature(gate)
            if feature:
                features.append(feature)
        return features

    @classmethod
    def gate_rows_for_template(cls, farm_id: str) -> list[dict]:
        rows = []
        for gate in cls.gates_for_farm(farm_id):
            rows.append(
                {
                    **cls.serialize_gate(gate),
                    "close_requirements": cls.close_requirements(gate),
                }
            )
        return rows
