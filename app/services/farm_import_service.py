from decimal import Decimal, ROUND_DOWN
from html import unescape
from math import cos, radians
from pathlib import Path
from tempfile import NamedTemporaryFile
import json
import re
import xml.etree.ElementTree as ET

from app.extensions import db
from app.models import (
    Farm,
    GrazingAllocation,
    GrazingAllocationLsuHistory,
    Paddock,
    WaterAsset,
)
from app.services.paddock_service import PaddockService
from app.services.water_network_service import WaterNetworkService

KML_NAMESPACE = "http://www.opengis.net/kml/2.2"
GX_NAMESPACE = "http://www.google.com/kml/ext/2.2"
KML_NAMESPACES = {"k": KML_NAMESPACE, "gx": GX_NAMESPACE}
EARTH_RADIUS_M = 6371008.8
INVALID_FARM_FILENAME_CHARS = set('<>:"/\\|?*')
FOUR_DECIMAL_PLACES = Decimal("0.0001")
SEVEN_DECIMAL_PLACES = Decimal("0.0000001")
GEOMETRY_EPSILON = 1e-9
WATER_ASSET_CODE_MAP = {
    "BH": "borehole",
    "PT": "pit",
    "WM": "windmill",
    "SP": "solarpump",
    "CD": "cement_dam",
    "TK": "tank",
    "GD": "ground_dam",
    "WR": "weir",
    "TR": "trough",
}


class FarmImportService:
    MAX_FARM_NAME_LENGTH = 120

    @classmethod
    def normalize_name(cls, value: str | None) -> str:
        return PaddockService.normalize_name(value)

    @classmethod
    def validate_farm_name(cls, value: str | None) -> str:
        name = cls.normalize_name(value)
        if not name:
            raise ValueError("Farm name derived from the file name is invalid")
        if len(name) > cls.MAX_FARM_NAME_LENGTH:
            raise ValueError("Farm name derived from the file name is invalid")
        if any(ord(char) < 32 for char in name):
            raise ValueError("Farm name derived from the file name is invalid")
        if any(char in INVALID_FARM_FILENAME_CHARS for char in name):
            raise ValueError("Farm name derived from the file name is invalid")
        if name.endswith((" ", ".")):
            raise ValueError("Farm name derived from the file name is invalid")
        return name

    @classmethod
    def _imported_water_asset_name_key(cls, asset_type: str, import_placemark_name: str | None):
        normalized_name = WaterNetworkService.normalize_name(import_placemark_name).casefold()
        if not normalized_name:
            return None
        return (asset_type, normalized_name)

    @staticmethod
    def _water_asset_location_key(asset_type: str, latitude, longitude):
        if latitude is None or longitude is None:
            return None
        normalized_latitude = Decimal(str(latitude)).quantize(SEVEN_DECIMAL_PLACES)
        normalized_longitude = Decimal(str(longitude)).quantize(SEVEN_DECIMAL_PLACES)
        return (asset_type, normalized_latitude, normalized_longitude)

    @staticmethod
    def _water_asset_import_match_rank(asset: WaterAsset) -> int:
        if asset.import_placemark_name and asset.active:
            return 0
        if asset.import_placemark_name:
            return 1
        if asset.active:
            return 2
        return 3

    @classmethod
    def _set_preferred_water_asset_match(cls, lookup: dict, key, asset: WaterAsset) -> None:
        if key is None:
            return
        current = lookup.get(key)
        if current is None or cls._water_asset_import_match_rank(asset) < cls._water_asset_import_match_rank(current):
            lookup[key] = asset

    @classmethod
    def _safe_upload_name(cls, file_name: str | None) -> str:
        raw_name = (file_name or "").strip()
        if not raw_name:
            return ""
        return raw_name.replace("\\", "/").split("/")[-1].strip()

    @classmethod
    def _farm_name_from_upload(cls, file_name: str | None) -> str:
        upload_name = cls._safe_upload_name(file_name)
        if not upload_name:
            raise ValueError("Select a .kml file to import")
        path = Path(upload_name)
        if path.suffix.lower() != ".kml":
            raise ValueError("Farm import requires a .kml file")
        return cls.validate_farm_name(path.stem)

    @staticmethod
    def _parse_root(file_bytes: bytes, parse_error_message: str):
        try:
            return ET.fromstring(file_bytes)
        except ET.ParseError as exc:
            raise ValueError(parse_error_message) from exc

    @classmethod
    def _identifier_tokens(cls, value: str | None) -> set[str]:
        raw = (value or "").strip()
        if not raw:
            return set()

        raw_tokens = [raw]
        separators = "#/?&=:.\\-_"
        working = raw
        for separator in separators:
            working = working.replace(separator, " ")
        raw_tokens.extend(working.split())

        tokens = set()
        for token in raw_tokens:
            normalized = "".join(char for char in token.upper() if char.isalnum())
            if normalized:
                tokens.add(normalized)
        return tokens

    @classmethod
    def _description_tokens(cls, value: str | None) -> set[str]:
        if not value:
            return set()
        text = unescape(value)
        text = re.sub(r"<[^>]+>", " ", text)
        return cls._identifier_tokens(text)

    @classmethod
    def _parse_managed_point_hints(cls, managed_point_hints) -> dict[str, str]:
        if not managed_point_hints:
            return {}
        if isinstance(managed_point_hints, dict):
            source = managed_point_hints
        else:
            try:
                source = json.loads(str(managed_point_hints))
            except (TypeError, ValueError):
                return {}
            if not isinstance(source, dict):
                return {}

        return {
            cls.normalize_name(key).casefold(): str(value).strip()
            for key, value in source.items()
            if cls.normalize_name(key) and str(value).strip()
        }

    @classmethod
    def _recognized_asset_type(
        cls,
        placemark_name: str,
        description_tokens: set[str],
        managed_point_hints: dict[str, str],
    ) -> str | None:
        for token in description_tokens:
            asset_type = WATER_ASSET_CODE_MAP.get(token)
            if asset_type:
                return asset_type

        hint = managed_point_hints.get(cls.normalize_name(placemark_name).casefold())
        if not hint:
            return None

        normalized_hint = WaterNetworkService.normalize_choice(hint)
        if normalized_hint in WaterNetworkService.ASSET_TYPES:
            return normalized_hint

        for token in cls._identifier_tokens(hint):
            asset_type = WATER_ASSET_CODE_MAP.get(token)
            if asset_type:
                return asset_type
        return None

    @staticmethod
    def _parse_point_coordinates(raw: str | None) -> tuple[float, float, float | None] | None:
        tokens = [token for token in (raw or "").replace("\n", " ").split() if token]
        if not tokens:
            return None
        parts = [part.strip() for part in tokens[0].split(",")]
        if len(parts) < 2:
            return None
        try:
            lon = float(parts[0])
            lat = float(parts[1])
            altitude = float(parts[2]) if len(parts) >= 3 and parts[2] else None
        except ValueError:
            return None
        return (lon, lat, altitude)

    @classmethod
    def _point_on_segment(
        cls,
        point: tuple[float, float],
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> bool:
        cross = cls._cross_product(start, end, point)
        if abs(cross) > GEOMETRY_EPSILON:
            return False
        if min(start[0], end[0]) - GEOMETRY_EPSILON <= point[0] <= max(start[0], end[0]) + GEOMETRY_EPSILON and min(
            start[1],
            end[1],
        ) - GEOMETRY_EPSILON <= point[1] <= max(start[1], end[1]) + GEOMETRY_EPSILON:
            return True
        return False

    @classmethod
    def _ring_contains_point(cls, point: tuple[float, float], ring: list[tuple[float, float]]) -> bool:
        open_ring = cls._open_ring(ring)
        if len(open_ring) < 3:
            return False

        inside = False
        for index, current in enumerate(open_ring):
            next_point = open_ring[(index + 1) % len(open_ring)]
            if cls._point_on_segment(point, current, next_point):
                return True
            y_crosses = (current[1] > point[1]) != (next_point[1] > point[1])
            if not y_crosses:
                continue
            xinters = ((next_point[0] - current[0]) * (point[1] - current[1]) / (next_point[1] - current[1])) + current[0]
            if point[0] < xinters + GEOMETRY_EPSILON:
                inside = not inside
        return inside

    @classmethod
    def _polygon_contains_point(cls, point: tuple[float, float], polygon: dict) -> bool:
        if not cls._ring_contains_point(point, polygon["outer"]):
            return False
        for inner_ring in polygon["inners"]:
            if cls._ring_contains_point(point, inner_ring):
                return False
        return True

    @classmethod
    def _geometry_contains_point(cls, point: tuple[float, float], geometry: list[dict]) -> bool:
        return any(cls._polygon_contains_point(point, polygon) for polygon in geometry)

    @classmethod
    def _containing_paddock_key(cls, point: tuple[float, float], paddocks: list[dict]) -> str | None:
        for paddock in paddocks:
            bounds = paddock["bounds"]
            if point[0] < bounds[0] or point[0] > bounds[2] or point[1] < bounds[1] or point[1] > bounds[3]:
                continue
            if cls._geometry_contains_point(point, paddock["geometry"]):
                return paddock["key"]
        return None

    @staticmethod
    def _distance_between_points(
        point: tuple[float, float],
        other: tuple[float, float],
    ) -> float:
        dx = other[0] - point[0]
        dy = other[1] - point[1]
        return (dx * dx + dy * dy) ** 0.5

    @classmethod
    def _distance_point_to_segment_m(
        cls,
        point: tuple[float, float],
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> float:
        segment_dx = end[0] - start[0]
        segment_dy = end[1] - start[1]
        segment_length_sq = (segment_dx * segment_dx) + (segment_dy * segment_dy)
        if segment_length_sq <= GEOMETRY_EPSILON:
            return cls._distance_between_points(point, start)

        projection = (
            ((point[0] - start[0]) * segment_dx) + ((point[1] - start[1]) * segment_dy)
        ) / segment_length_sq
        projection = max(0.0, min(1.0, projection))
        closest_point = (
            start[0] + (projection * segment_dx),
            start[1] + (projection * segment_dy),
        )
        return cls._distance_between_points(point, closest_point)

    @classmethod
    def _distance_point_to_ring_m(
        cls,
        point: tuple[float, float],
        ring: list[tuple[float, float]],
    ) -> float:
        open_ring = cls._open_ring(ring)
        if len(open_ring) < 2:
            return float("inf")

        lat_values = [point[1], *[lat for _, lat in open_ring]]
        lat0 = sum(lat_values) / len(lat_values)
        projected_point = cls._project_lon_lat_points([point], lat0)[0]
        projected_ring = cls._project_lon_lat_points(open_ring, lat0)

        minimum_distance = float("inf")
        for index, start in enumerate(projected_ring):
            end = projected_ring[(index + 1) % len(projected_ring)]
            minimum_distance = min(
                minimum_distance,
                cls._distance_point_to_segment_m(projected_point, start, end),
            )
        return minimum_distance

    @classmethod
    def _distance_point_to_geometry_m(
        cls,
        point: tuple[float, float],
        geometry: list[dict],
    ) -> float:
        if cls._geometry_contains_point(point, geometry):
            return 0.0

        minimum_distance = float("inf")
        for polygon in geometry:
            minimum_distance = min(
                minimum_distance,
                cls._distance_point_to_ring_m(point, polygon["outer"]),
            )
            for inner_ring in polygon["inners"]:
                minimum_distance = min(
                    minimum_distance,
                    cls._distance_point_to_ring_m(point, inner_ring),
                )
        return minimum_distance

    @classmethod
    def _nearest_paddock_key(
        cls,
        point: tuple[float, float],
        paddocks: list[dict],
        *,
        max_distance_m: float,
    ) -> str | None:
        closest_key = None
        closest_distance = None
        for paddock in paddocks:
            distance = cls._distance_point_to_geometry_m(point, paddock["geometry"])
            if distance > max_distance_m:
                continue
            if closest_distance is None or distance < closest_distance:
                closest_key = paddock["key"]
                closest_distance = distance
        return closest_key

    @staticmethod
    def _parse_ring_coordinates(raw: str | None, paddock_name: str) -> list[tuple[float, float]]:
        tokens = [token for token in (raw or "").replace("\n", " ").split() if token]
        coords: list[tuple[float, float]] = []
        for token in tokens:
            parts = [part.strip() for part in token.split(",")]
            if len(parts) < 2:
                raise ValueError(f"Unable to calculate area for paddock {paddock_name}")
            try:
                lon = float(parts[0])
                lat = float(parts[1])
            except ValueError as exc:
                raise ValueError(f"Unable to calculate area for paddock {paddock_name}") from exc
            coords.append((lon, lat))

        if len(coords) < 3:
            raise ValueError(f"Unable to calculate area for paddock {paddock_name}")
        if coords[0] != coords[-1]:
            coords.append(coords[0])
        if len(coords) < 4:
            raise ValueError(f"Unable to calculate area for paddock {paddock_name}")
        return coords

    @staticmethod
    def _open_ring(coords: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if len(coords) >= 2 and coords[0] == coords[-1]:
            return list(coords[:-1])
        return list(coords)

    @classmethod
    def _project_lon_lat_points(
        cls,
        points: list[tuple[float, float]],
        lat0: float,
    ) -> list[tuple[float, float]]:
        cos_lat0 = cos(radians(lat0))
        return [
            (
                EARTH_RADIUS_M * radians(lon) * cos_lat0,
                EARTH_RADIUS_M * radians(lat),
            )
            for lon, lat in points
        ]

    @staticmethod
    def _signed_area(points: list[tuple[float, float]]) -> float:
        if len(points) < 3:
            return 0.0
        area = 0.0
        for index, point in enumerate(points):
            next_point = points[(index + 1) % len(points)]
            area += (point[0] * next_point[1]) - (next_point[0] * point[1])
        return area / 2.0

    @classmethod
    def _projected_ring_area_m2(cls, coords: list[tuple[float, float]], paddock_name: str) -> float:
        open_ring = cls._open_ring(coords)
        if len(open_ring) < 3:
            raise ValueError(f"Unable to calculate area for paddock {paddock_name}")

        lat0 = sum(lat for _, lat in open_ring) / len(open_ring)
        projected = cls._project_lon_lat_points(open_ring, lat0)
        area = abs(cls._signed_area(projected))
        if area <= 0:
            raise ValueError(f"Unable to calculate area for paddock {paddock_name}")
        return area

    @staticmethod
    def _points_close(left: tuple[float, float], right: tuple[float, float]) -> bool:
        return abs(left[0] - right[0]) <= GEOMETRY_EPSILON and abs(left[1] - right[1]) <= GEOMETRY_EPSILON

    @staticmethod
    def _cross_product(
        left: tuple[float, float],
        middle: tuple[float, float],
        right: tuple[float, float],
    ) -> float:
        return ((middle[0] - left[0]) * (right[1] - left[1])) - (
            (middle[1] - left[1]) * (right[0] - left[0])
        )

    @classmethod
    def _clean_projected_ring(cls, points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if not points:
            return []

        deduped: list[tuple[float, float]] = []
        for point in points:
            if not deduped or not cls._points_close(deduped[-1], point):
                deduped.append(point)
        if len(deduped) > 1 and cls._points_close(deduped[0], deduped[-1]):
            deduped.pop()

        if len(deduped) < 3:
            return deduped

        cleaned: list[tuple[float, float]] = []
        total_points = len(deduped)
        for index, point in enumerate(deduped):
            previous_point = deduped[index - 1]
            next_point = deduped[(index + 1) % total_points]
            cross = cls._cross_product(previous_point, point, next_point)
            if abs(cross) <= GEOMETRY_EPSILON:
                continue
            cleaned.append(point)
        return cleaned

    @classmethod
    def _triangle_contains_point(
        cls,
        point: tuple[float, float],
        a: tuple[float, float],
        b: tuple[float, float],
        c: tuple[float, float],
    ) -> bool:
        d1 = cls._cross_product(point, a, b)
        d2 = cls._cross_product(point, b, c)
        d3 = cls._cross_product(point, c, a)
        has_negative = d1 < -GEOMETRY_EPSILON or d2 < -GEOMETRY_EPSILON or d3 < -GEOMETRY_EPSILON
        has_positive = d1 > GEOMETRY_EPSILON or d2 > GEOMETRY_EPSILON or d3 > GEOMETRY_EPSILON
        return not (has_negative and has_positive)

    @classmethod
    def _triangulate_ring(
        cls,
        points: list[tuple[float, float]],
        paddock_name: str,
    ) -> list[list[tuple[float, float]]]:
        ring = cls._clean_projected_ring(points)
        if len(ring) < 3:
            raise ValueError(f"Unable to calculate area for paddock {paddock_name}")

        if cls._signed_area(ring) < 0:
            ring = list(reversed(ring))

        if len(ring) == 3:
            return [ring]

        indices = list(range(len(ring)))
        triangles: list[list[tuple[float, float]]] = []
        guard = 0
        max_guard = len(ring) * len(ring) * 4

        while len(indices) > 3 and guard <= max_guard:
            ear_found = False
            index_count = len(indices)
            for position in range(index_count):
                prev_index = indices[(position - 1) % index_count]
                current_index = indices[position]
                next_index = indices[(position + 1) % index_count]
                prev_point = ring[prev_index]
                current_point = ring[current_index]
                next_point = ring[next_index]

                if cls._cross_product(prev_point, current_point, next_point) <= GEOMETRY_EPSILON:
                    continue
                triangle = [prev_point, current_point, next_point]
                if abs(cls._signed_area(triangle)) <= GEOMETRY_EPSILON:
                    continue

                contains_other_vertex = False
                for candidate_index in indices:
                    if candidate_index in {prev_index, current_index, next_index}:
                        continue
                    if cls._triangle_contains_point(ring[candidate_index], *triangle):
                        contains_other_vertex = True
                        break
                if contains_other_vertex:
                    continue

                triangles.append(triangle)
                indices.pop(position)
                ear_found = True
                break

            if not ear_found:
                raise ValueError(f"Unable to calculate area for paddock {paddock_name}")
            guard += 1

        if len(indices) != 3:
            raise ValueError(f"Unable to calculate area for paddock {paddock_name}")
        triangles.append([ring[indices[0]], ring[indices[1]], ring[indices[2]]])
        return triangles

    @staticmethod
    def _line_intersection(
        start: tuple[float, float],
        end: tuple[float, float],
        clip_start: tuple[float, float],
        clip_end: tuple[float, float],
    ) -> tuple[float, float]:
        x1, y1 = start
        x2, y2 = end
        x3, y3 = clip_start
        x4, y4 = clip_end
        denominator = ((x1 - x2) * (y3 - y4)) - ((y1 - y2) * (x3 - x4))
        if abs(denominator) <= GEOMETRY_EPSILON:
            return end
        determinant_start = (x1 * y2) - (y1 * x2)
        determinant_clip = (x3 * y4) - (y3 * x4)
        x = ((determinant_start * (x3 - x4)) - ((x1 - x2) * determinant_clip)) / denominator
        y = ((determinant_start * (y3 - y4)) - ((y1 - y2) * determinant_clip)) / denominator
        return (x, y)

    @classmethod
    def _clip_polygon_to_edge(
        cls,
        polygon: list[tuple[float, float]],
        clip_start: tuple[float, float],
        clip_end: tuple[float, float],
    ) -> list[tuple[float, float]]:
        if not polygon:
            return []

        clipped: list[tuple[float, float]] = []

        def is_inside(point: tuple[float, float]) -> bool:
            return cls._cross_product(clip_start, clip_end, point) >= -GEOMETRY_EPSILON

        previous_point = polygon[-1]
        previous_inside = is_inside(previous_point)

        for current_point in polygon:
            current_inside = is_inside(current_point)
            if current_inside:
                if not previous_inside:
                    clipped.append(
                        cls._line_intersection(previous_point, current_point, clip_start, clip_end)
                    )
                clipped.append(current_point)
            elif previous_inside:
                clipped.append(
                    cls._line_intersection(previous_point, current_point, clip_start, clip_end)
                )
            previous_point = current_point
            previous_inside = current_inside

        return clipped

    @classmethod
    def _triangle_intersection_area(
        cls,
        left_triangle: list[tuple[float, float]],
        right_triangle: list[tuple[float, float]],
    ) -> float:
        polygon = list(left_triangle)
        for index, point in enumerate(right_triangle):
            next_point = right_triangle[(index + 1) % len(right_triangle)]
            polygon = cls._clip_polygon_to_edge(polygon, point, next_point)
            if len(polygon) < 3:
                return 0.0
        return abs(cls._signed_area(polygon))

    @classmethod
    def _simple_polygon_intersection_area_m2(
        cls,
        left_ring: list[tuple[float, float]],
        right_ring: list[tuple[float, float]],
        paddock_name: str,
    ) -> float:
        left_points = cls._open_ring(left_ring)
        right_points = cls._open_ring(right_ring)
        if len(left_points) < 3 or len(right_points) < 3:
            return 0.0

        lat_values = [lat for _, lat in left_points + right_points]
        lat0 = sum(lat_values) / len(lat_values)
        projected_left = cls._project_lon_lat_points(left_points, lat0)
        projected_right = cls._project_lon_lat_points(right_points, lat0)

        left_triangles = cls._triangulate_ring(projected_left, paddock_name)
        right_triangles = cls._triangulate_ring(projected_right, paddock_name)

        intersection_area = 0.0
        for left_triangle in left_triangles:
            left_xs = [point[0] for point in left_triangle]
            left_ys = [point[1] for point in left_triangle]
            left_bounds = (min(left_xs), min(left_ys), max(left_xs), max(left_ys))

            for right_triangle in right_triangles:
                right_xs = [point[0] for point in right_triangle]
                right_ys = [point[1] for point in right_triangle]
                right_bounds = (min(right_xs), min(right_ys), max(right_xs), max(right_ys))
                if not cls._bounds_intersect(left_bounds, right_bounds):
                    continue
                intersection_area += cls._triangle_intersection_area(left_triangle, right_triangle)

        return intersection_area

    @classmethod
    def _parse_polygon_geometry(cls, polygon, paddock_name: str) -> dict:
        outer_text = polygon.findtext(
            "k:outerBoundaryIs/k:LinearRing/k:coordinates",
            default="",
            namespaces=KML_NAMESPACES,
        )
        outer_coords = cls._parse_ring_coordinates(outer_text, paddock_name)
        outer_area = cls._projected_ring_area_m2(outer_coords, paddock_name)
        if outer_area <= 0:
            raise ValueError(f"Unable to calculate area for paddock {paddock_name}")

        inner_rings = []
        inner_area = 0.0
        for inner_boundary in polygon.findall("k:innerBoundaryIs", KML_NAMESPACES):
            inner_text = inner_boundary.findtext(
                "k:LinearRing/k:coordinates",
                default="",
                namespaces=KML_NAMESPACES,
            )
            inner_coords = cls._parse_ring_coordinates(inner_text, paddock_name)
            inner_rings.append(inner_coords)
            inner_area += cls._projected_ring_area_m2(inner_coords, paddock_name)

        total_area_m2 = outer_area - inner_area
        if total_area_m2 <= 0:
            raise ValueError(f"Unable to calculate area for paddock {paddock_name}")

        return {"outer": outer_coords, "inners": inner_rings, "area_m2": total_area_m2}

    @classmethod
    def _placemark_geometry(cls, placemark, paddock_name: str) -> list[dict]:
        polygons = placemark.findall(".//k:Polygon", KML_NAMESPACES)
        if not polygons:
            raise ValueError(f"Unable to calculate area for paddock {paddock_name}")
        geometry = [cls._parse_polygon_geometry(polygon, paddock_name) for polygon in polygons]
        if not geometry:
            raise ValueError(f"Unable to calculate area for paddock {paddock_name}")
        return geometry

    @classmethod
    def _geometry_bounds(cls, geometry: list[dict]) -> tuple[float, float, float, float]:
        lons = []
        lats = []
        for polygon in geometry:
            for lon, lat in cls._open_ring(polygon["outer"]):
                lons.append(lon)
                lats.append(lat)
        return (min(lons), min(lats), max(lons), max(lats))

    @staticmethod
    def _bounds_intersect(
        left_bounds: tuple[float, float, float, float],
        right_bounds: tuple[float, float, float, float],
    ) -> bool:
        return not (
            left_bounds[2] <= right_bounds[0]
            or right_bounds[2] <= left_bounds[0]
            or left_bounds[3] <= right_bounds[1]
            or right_bounds[3] <= left_bounds[1]
        )

    @classmethod
    def _polygon_overlap_area_m2(
        cls,
        left_polygon: dict,
        right_polygon: dict,
        paddock_name: str,
    ) -> float:
        overlap = cls._simple_polygon_intersection_area_m2(
            left_polygon["outer"],
            right_polygon["outer"],
            paddock_name,
        )
        if overlap <= 0:
            return 0.0

        for left_hole in left_polygon["inners"]:
            overlap -= cls._simple_polygon_intersection_area_m2(
                left_hole,
                right_polygon["outer"],
                paddock_name,
            )

        for right_hole in right_polygon["inners"]:
            overlap -= cls._simple_polygon_intersection_area_m2(
                left_polygon["outer"],
                right_hole,
                paddock_name,
            )

        for left_hole in left_polygon["inners"]:
            for right_hole in right_polygon["inners"]:
                overlap += cls._simple_polygon_intersection_area_m2(
                    left_hole,
                    right_hole,
                    paddock_name,
                )

        return max(0.0, overlap)

    @classmethod
    def _geometry_overlap_area_hectares(
        cls,
        left_geometry: list[dict],
        right_geometry: list[dict],
        paddock_name: str,
    ) -> float:
        total_overlap_m2 = 0.0
        for left_polygon in left_geometry:
            left_bounds = cls._geometry_bounds([left_polygon])
            for right_polygon in right_geometry:
                right_bounds = cls._geometry_bounds([right_polygon])
                if not cls._bounds_intersect(left_bounds, right_bounds):
                    continue
                total_overlap_m2 += cls._polygon_overlap_area_m2(left_polygon, right_polygon, paddock_name)
        return total_overlap_m2 / 10000.0

    @classmethod
    def _parse_kml_paddocks_from_root(cls, *, root, farm_name: str) -> list[dict]:
        paddocks = []
        seen_paddock_names = set()
        farm_name_key = cls.normalize_name(farm_name).casefold()
        for placemark in root.findall(".//k:Placemark", KML_NAMESPACES):
            placemark_name = (
                placemark.findtext("k:name", default="", namespaces=KML_NAMESPACES) or ""
            ).strip()
            polygons = placemark.findall(".//k:Polygon", KML_NAMESPACES)
            if not placemark_name or not polygons:
                continue
            if cls.normalize_name(placemark_name).casefold() == farm_name_key:
                continue

            paddock_name = PaddockService.validate_name(placemark_name)
            paddock_key = cls.normalize_name(paddock_name).casefold()
            if paddock_key in seen_paddock_names:
                raise ValueError("Duplicate paddock names were found in the uploaded KML")

            geometry = cls._placemark_geometry(placemark, paddock_name)
            area_ha = round(sum(float(polygon["area_m2"]) for polygon in geometry) / 10000.0, 2)
            if area_ha <= 0:
                raise ValueError(f"Unable to calculate area for paddock {paddock_name}")

            paddocks.append(
                {
                    "key": paddock_key,
                    "name": paddock_name,
                    "area_ha": area_ha,
                    "grazeable_area_ha": area_ha,
                    "geometry": geometry,
                    "bounds": cls._geometry_bounds(geometry),
                }
            )
            seen_paddock_names.add(paddock_key)

        if not paddocks:
            raise ValueError("No paddock polygons were found in the uploaded KML")

        return paddocks

    @classmethod
    def _parse_kml_paddocks(
        cls,
        *,
        file_bytes: bytes,
        farm_name: str,
        parse_error_message: str,
    ) -> list[dict]:
        root = cls._parse_root(file_bytes, parse_error_message)
        return cls._parse_kml_paddocks_from_root(root=root, farm_name=farm_name)

    @classmethod
    def _parse_kml_water_assets_from_root(
        cls,
        *,
        root,
        paddocks: list[dict],
        managed_point_hints: dict[str, str],
    ) -> list[dict]:
        water_assets = []

        for placemark in root.findall(".//k:Placemark", KML_NAMESPACES):
            placemark_name = (
                placemark.findtext("k:name", default="", namespaces=KML_NAMESPACES) or ""
            ).strip()
            point_coordinates = cls._parse_point_coordinates(
                placemark.findtext(".//k:Point/k:coordinates", default="", namespaces=KML_NAMESPACES)
            )
            if not placemark_name or point_coordinates is None:
                continue

            style_url = (placemark.findtext("k:styleUrl", default="", namespaces=KML_NAMESPACES) or "").strip()
            description_tokens = cls._description_tokens(
                placemark.findtext("k:description", default="", namespaces=KML_NAMESPACES)
            )
            asset_type = cls._recognized_asset_type(
                placemark_name,
                description_tokens,
                managed_point_hints,
            )
            if not asset_type:
                continue

            validated_name = WaterNetworkService.validate_name(placemark_name)
            containing_paddock_key = cls._containing_paddock_key(
                (point_coordinates[0], point_coordinates[1]),
                paddocks,
            )
            if containing_paddock_key is None and asset_type in WaterNetworkService.SERVED_PADDOCK_TYPES:
                containing_paddock_key = cls._nearest_paddock_key(
                    (point_coordinates[0], point_coordinates[1]),
                    paddocks,
                    max_distance_m=250.0,
                )
            water_assets.append(
                {
                    "name": validated_name,
                    "asset_type": asset_type,
                    "latitude": point_coordinates[1],
                    "longitude": point_coordinates[0],
                    "altitude_m": point_coordinates[2],
                    "location_paddock_key": containing_paddock_key,
                    "served_paddock_keys": (
                        [containing_paddock_key]
                        if asset_type in WaterNetworkService.SERVED_PADDOCK_TYPES and containing_paddock_key
                        else []
                    ),
                    "import_placemark_name": validated_name,
                    "import_style_url": style_url or None,
                }
            )

        return water_assets

    @classmethod
    def parse_upload(
        cls,
        file_name: str,
        file_bytes: bytes,
        *,
        managed_point_hints: dict[str, str] | None = None,
    ) -> dict:
        farm_name = cls._farm_name_from_upload(file_name)
        if not file_bytes:
            raise ValueError("Uploaded KML file is empty")

        root = cls._parse_root(file_bytes, "Unable to parse the uploaded KML file")
        paddocks = cls._parse_kml_paddocks_from_root(root=root, farm_name=farm_name)
        water_assets = cls._parse_kml_water_assets_from_root(
            root=root,
            paddocks=paddocks,
            managed_point_hints=cls._parse_managed_point_hints(managed_point_hints),
        )
        return {"farm_name": farm_name, "paddocks": paddocks, "water_assets": water_assets}

    @classmethod
    def _load_existing_map_paddocks(cls, map_path: Path, farm_name: str) -> dict[str, dict]:
        if not map_path.exists():
            raise ValueError("Unable to read the existing farm map to transfer missing paddock history")

        paddocks = cls._parse_kml_paddocks(
            file_bytes=map_path.read_bytes(),
            farm_name=farm_name,
            parse_error_message="Unable to read the existing farm map to transfer missing paddock history",
        )
        return {payload["key"]: payload for payload in paddocks}

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
            return [Decimal("0")] * len(ratios)

        total_units = cls._decimal_units(total_value, quantum)
        if total_units <= 0:
            return [Decimal("0")] * len(ratios)

        normalized = [Decimal(str(ratio / positive_total)) for ratio in positive_ratios]
        base_units: list[int] = []
        remainders: list[tuple[Decimal, int]] = []
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
    def _replacement_targets(cls, source_payload: dict, imported_paddocks: list[dict]) -> list[dict]:
        overlaps = []
        for paddock_payload in imported_paddocks:
            if not cls._bounds_intersect(source_payload["bounds"], paddock_payload["bounds"]):
                continue
            overlap_ha = cls._geometry_overlap_area_hectares(
                source_payload["geometry"],
                paddock_payload["geometry"],
                source_payload["name"],
            )
            if overlap_ha > 0:
                overlaps.append({"paddock": paddock_payload["db_paddock"], "overlap_ha": overlap_ha})

        total_overlap = sum(item["overlap_ha"] for item in overlaps)
        if total_overlap <= 0:
            return []

        return [
            {
                "paddock": item["paddock"],
                "ratio": item["overlap_ha"] / total_overlap,
            }
            for item in overlaps
        ]

    @classmethod
    def _rebuild_session_history(
        cls,
        *,
        affected_session_ids: list[str],
        allocation_transfer_map: dict[str, list[dict]],
    ) -> None:
        if not affected_session_ids:
            return

        rows = (
            GrazingAllocationLsuHistory.query.filter(
                GrazingAllocationLsuHistory.grazing_session_id.in_(affected_session_ids)
            )
            .order_by(
                GrazingAllocationLsuHistory.effective_from.asc(),
                GrazingAllocationLsuHistory.created_at.asc(),
            )
            .all()
        )

        merged_rows: dict[tuple, dict] = {}
        for row in rows:
            transfer_targets = allocation_transfer_map.get(str(row.grazing_allocation_id))
            if transfer_targets:
                fraction_splits = cls._split_decimal_value(
                    Decimal(str(row.allocation_fraction)),
                    [item["ratio"] for item in transfer_targets],
                    quantum=FOUR_DECIMAL_PLACES,
                )
                lsu_splits = cls._split_decimal_value(
                    Decimal(str(row.allocated_lsu)),
                    [item["ratio"] for item in transfer_targets],
                    quantum=FOUR_DECIMAL_PLACES,
                )
                row_payloads = []
                for target, fraction_split, lsu_split in zip(
                    transfer_targets, fraction_splits, lsu_splits
                ):
                    if fraction_split <= 0 or lsu_split <= 0:
                        continue
                    allocation = target["allocation"]
                    row_payloads.append(
                        {
                            "farm_id": row.farm_id,
                            "mob_id": row.mob_id,
                            "paddock_id": allocation.paddock_id,
                            "grazing_session_id": row.grazing_session_id,
                            "grazing_allocation_id": allocation.id,
                            "effective_from": row.effective_from,
                            "effective_to": row.effective_to,
                            "allocation_fraction": fraction_split,
                            "mob_total_lsu": Decimal(str(row.mob_total_lsu)),
                            "allocated_lsu": lsu_split,
                            "source": row.source,
                        }
                    )
            else:
                row_payloads = [
                    {
                        "farm_id": row.farm_id,
                        "mob_id": row.mob_id,
                        "paddock_id": row.paddock_id,
                        "grazing_session_id": row.grazing_session_id,
                        "grazing_allocation_id": row.grazing_allocation_id,
                        "effective_from": row.effective_from,
                        "effective_to": row.effective_to,
                        "allocation_fraction": Decimal(str(row.allocation_fraction)),
                        "mob_total_lsu": Decimal(str(row.mob_total_lsu)),
                        "allocated_lsu": Decimal(str(row.allocated_lsu)),
                        "source": row.source,
                    }
                ]

            for payload in row_payloads:
                key = (
                    str(payload["grazing_allocation_id"]),
                    payload["effective_from"],
                    payload["effective_to"],
                    payload["source"],
                )
                existing = merged_rows.get(key)
                if existing is None:
                    merged_rows[key] = payload
                    continue
                existing["allocation_fraction"] += payload["allocation_fraction"]
                existing["allocated_lsu"] += payload["allocated_lsu"]

        GrazingAllocationLsuHistory.query.filter(
            GrazingAllocationLsuHistory.grazing_session_id.in_(affected_session_ids)
        ).delete(synchronize_session=False)

        for payload in merged_rows.values():
            db.session.add(
                GrazingAllocationLsuHistory(
                    farm_id=payload["farm_id"],
                    mob_id=payload["mob_id"],
                    paddock_id=payload["paddock_id"],
                    grazing_session_id=payload["grazing_session_id"],
                    grazing_allocation_id=payload["grazing_allocation_id"],
                    effective_from=payload["effective_from"],
                    effective_to=payload["effective_to"],
                    allocation_fraction=payload["allocation_fraction"],
                    mob_total_lsu=payload["mob_total_lsu"],
                    allocated_lsu=payload["allocated_lsu"],
                    source=payload["source"],
                )
            )

    @classmethod
    def import_farm(
        cls,
        file_name: str,
        file_bytes: bytes,
        timezone: str,
        instance_path: str | Path,
        *,
        managed_point_hints: dict[str, str] | None = None,
    ) -> dict:
        parsed = cls.parse_upload(
            file_name,
            file_bytes,
            managed_point_hints=managed_point_hints,
        )
        farm_name = parsed["farm_name"]
        timezone_value = cls.normalize_name(timezone) or "UTC"
        map_path = Path(instance_path) / "maps" / f"{farm_name}.kml"
        farm = Farm.query.filter_by(name=farm_name).first()
        if farm is None and map_path.exists():
            raise ValueError(f"A map file for {farm_name} already exists")

        maps_dir = map_path.parent
        maps_dir.mkdir(parents=True, exist_ok=True)

        created_count = 0
        updated_count = 0
        retired_count = 0
        water_created_count = 0
        water_updated_count = 0
        water_archived_count = 0
        existing_farm = farm is not None
        existing_by_name: dict[str, Paddock] = {}
        if farm is None:
            farm = Farm(name=farm_name, timezone=timezone_value, active=True)
            db.session.add(farm)
            db.session.flush()
        else:
            farm.timezone = timezone_value
            farm.active = True
            existing_by_name = {
                cls.normalize_name(paddock.name).casefold(): paddock
                for paddock in Paddock.query.filter_by(farm_id=farm.id).all()
            }

        imported_by_key = {payload["key"]: payload for payload in parsed["paddocks"]}
        for paddock_payload in parsed["paddocks"]:
            existing_paddock = existing_by_name.get(paddock_payload["key"])
            if existing_paddock is None:
                existing_paddock = Paddock(
                    farm_id=farm.id,
                    name=paddock_payload["name"],
                    area_ha=paddock_payload["area_ha"],
                    grazeable_area_ha=paddock_payload["grazeable_area_ha"],
                    status="active",
                )
                db.session.add(existing_paddock)
                created_count += 1
            else:
                changed = False
                if existing_paddock.name != paddock_payload["name"]:
                    existing_paddock.name = paddock_payload["name"]
                    changed = True
                if float(existing_paddock.area_ha) != float(paddock_payload["area_ha"]):
                    existing_paddock.area_ha = paddock_payload["area_ha"]
                    changed = True
                if float(existing_paddock.grazeable_area_ha) != float(
                    paddock_payload["grazeable_area_ha"]
                ):
                    existing_paddock.grazeable_area_ha = paddock_payload["grazeable_area_ha"]
                    changed = True
                if existing_paddock.status != "active":
                    existing_paddock.status = "active"
                    changed = True
                if changed:
                    updated_count += 1
            paddock_payload["db_paddock"] = existing_paddock

        db.session.flush()

        allocation_transfer_map: dict[str, list[dict]] = {}
        allocations_to_delete: list[GrazingAllocation] = []
        affected_session_ids: set[str] = set()

        if existing_farm:
            missing_paddocks = [
                paddock
                for key, paddock in existing_by_name.items()
                if key not in imported_by_key and paddock.status == "active"
            ]
            source_allocations = (
                GrazingAllocation.query.filter(
                    GrazingAllocation.paddock_id.in_([paddock.id for paddock in missing_paddocks])
                ).all()
                if missing_paddocks
                else []
            )
            source_allocations_by_paddock: dict[str, list[GrazingAllocation]] = {}
            for allocation in source_allocations:
                source_allocations_by_paddock.setdefault(str(allocation.paddock_id), []).append(allocation)

            paddocks_needing_transfer = [
                paddock
                for paddock in missing_paddocks
                if source_allocations_by_paddock.get(str(paddock.id))
            ]
            existing_map_paddocks = (
                cls._load_existing_map_paddocks(map_path, farm_name) if paddocks_needing_transfer else {}
            )

            transfer_plans = []
            for paddock in missing_paddocks:
                allocations = source_allocations_by_paddock.get(str(paddock.id), [])
                if not allocations:
                    paddock.status = "inactive"
                    retired_count += 1
                    continue

                source_key = cls.normalize_name(paddock.name).casefold()
                source_payload = existing_map_paddocks.get(source_key)
                if source_payload is None:
                    raise ValueError(
                        f"Unable to transfer grazing history for missing paddock {paddock.name}"
                    )

                replacement_targets = cls._replacement_targets(source_payload, parsed["paddocks"])
                if not replacement_targets:
                    raise ValueError(
                        f"Unable to match replacement paddocks for missing paddock {paddock.name}"
                    )

                transfer_plans.append(
                    {
                        "paddock": paddock,
                        "allocations": allocations,
                        "targets": replacement_targets,
                    }
                )

            if transfer_plans:
                affected_session_ids = {
                    str(allocation.grazing_session_id)
                    for plan in transfer_plans
                    for allocation in plan["allocations"]
                }
                existing_session_allocations = {
                    (str(allocation.grazing_session_id), str(allocation.paddock_id)): allocation
                    for allocation in GrazingAllocation.query.filter(
                        GrazingAllocation.grazing_session_id.in_(list(affected_session_ids))
                    ).all()
                }

                for plan in transfer_plans:
                    for source_allocation in plan["allocations"]:
                        split_fractions = cls._split_decimal_value(
                            Decimal(str(source_allocation.allocation_fraction)),
                            [target["ratio"] for target in plan["targets"]],
                            quantum=FOUR_DECIMAL_PLACES,
                        )
                        target_entries = []
                        for target, split_fraction in zip(plan["targets"], split_fractions):
                            if split_fraction <= 0:
                                continue
                            target_paddock = target["paddock"]
                            allocation_key = (
                                str(source_allocation.grazing_session_id),
                                str(target_paddock.id),
                            )
                            target_allocation = existing_session_allocations.get(allocation_key)
                            if target_allocation is None:
                                target_allocation = GrazingAllocation(
                                    grazing_session_id=source_allocation.grazing_session_id,
                                    paddock_id=target_paddock.id,
                                    allocation_fraction=split_fraction,
                                )
                                db.session.add(target_allocation)
                                existing_session_allocations[allocation_key] = target_allocation
                            else:
                                target_allocation.allocation_fraction = Decimal(
                                    str(target_allocation.allocation_fraction)
                                ) + split_fraction
                            target_entries.append(
                                {"allocation": target_allocation, "ratio": target["ratio"]}
                            )

                        if not target_entries:
                            raise ValueError(
                                f"Unable to transfer grazing history for missing paddock {plan['paddock'].name}"
                            )

                        allocation_transfer_map[str(source_allocation.id)] = target_entries
                        allocations_to_delete.append(source_allocation)

                    plan["paddock"].status = "inactive"
                    retired_count += 1

                db.session.flush()
                cls._rebuild_session_history(
                    affected_session_ids=list(affected_session_ids),
                    allocation_transfer_map=allocation_transfer_map,
                )
                for allocation in allocations_to_delete:
                    db.session.delete(allocation)

        existing_water_assets = WaterAsset.query.filter_by(farm_id=farm.id).all()
        preexisting_imported_water_assets = [
            asset for asset in existing_water_assets if asset.import_placemark_name
        ]
        imported_water_assets_by_name = {}
        water_assets_by_location = {}
        for asset in existing_water_assets:
            cls._set_preferred_water_asset_match(
                water_assets_by_location,
                cls._water_asset_location_key(asset.asset_type, asset.latitude, asset.longitude),
                asset,
            )
            if asset.import_placemark_name:
                cls._set_preferred_water_asset_match(
                    imported_water_assets_by_name,
                    cls._imported_water_asset_name_key(asset.asset_type, asset.import_placemark_name),
                    asset,
                )
        paddock_id_by_key = {
            paddock_payload["key"]: str(paddock_payload["db_paddock"].id)
            for paddock_payload in parsed["paddocks"]
        }
        seen_preexisting_imported_water_asset_ids = set()

        for water_payload in parsed.get("water_assets", []):
            water_name_key = cls._imported_water_asset_name_key(
                water_payload["asset_type"],
                water_payload["import_placemark_name"],
            )
            water_location_key = cls._water_asset_location_key(
                water_payload["asset_type"],
                water_payload["latitude"],
                water_payload["longitude"],
            )
            asset_payload = {
                "farm_id": str(farm.id),
                "name": water_payload["name"],
                "asset_type": water_payload["asset_type"],
                "active": True,
                "needs_review": True,
                "location_paddock_id": paddock_id_by_key.get(water_payload.get("location_paddock_key")),
                "latitude": water_payload["latitude"],
                "longitude": water_payload["longitude"],
                "altitude_m": water_payload["altitude_m"],
                "import_placemark_name": water_payload["import_placemark_name"],
                "import_style_url": water_payload.get("import_style_url"),
                "served_paddock_ids": [
                    paddock_id_by_key[key]
                    for key in water_payload.get("served_paddock_keys", [])
                    if key in paddock_id_by_key
                ],
            }
            existing_asset = imported_water_assets_by_name.get(water_name_key)
            if existing_asset is None:
                existing_asset = water_assets_by_location.get(water_location_key)
            if existing_asset is None:
                existing_asset = WaterNetworkService.create_asset(asset_payload, imported=True)
                water_created_count += 1
            else:
                WaterNetworkService.apply_asset_payload(existing_asset, asset_payload, imported=True)
                water_updated_count += 1
            imported_water_assets_by_name[water_name_key] = existing_asset
            water_assets_by_location[water_location_key] = existing_asset
            if existing_asset.import_placemark_name:
                seen_preexisting_imported_water_asset_ids.add(str(existing_asset.id))

        for existing_asset in preexisting_imported_water_assets:
            if str(existing_asset.id) in seen_preexisting_imported_water_asset_ids or not existing_asset.active:
                continue
            WaterNetworkService.apply_asset_payload(
                existing_asset,
                {
                    "farm_id": str(farm.id),
                    "name": existing_asset.name,
                    "asset_type": existing_asset.asset_type,
                    "active": False,
                    "needs_review": True,
                },
                imported=True,
            )
            water_archived_count += 1

        WaterNetworkService.ensure_default_trough_connections(
            str(farm.id),
            source_asset_types=WaterNetworkService.IMPORT_DEFAULT_TROUGH_CONNECTION_SOURCE_TYPES,
        )

        temp_path = None
        final_written = False
        previous_map_bytes = map_path.read_bytes() if map_path.exists() else None
        try:
            with NamedTemporaryFile(mode="wb", dir=maps_dir, suffix=".kml.tmp", delete=False) as temp_file:
                temp_file.write(file_bytes)
                temp_path = Path(temp_file.name)
            temp_path.replace(map_path)
            final_written = True
            db.session.commit()
        except Exception:
            db.session.rollback()
            if final_written:
                if previous_map_bytes is None:
                    map_path.unlink(missing_ok=True)
                else:
                    map_path.write_bytes(previous_map_bytes)
            raise
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink(missing_ok=True)

        return {
            "farm_id": str(farm.id),
            "farm_name": farm_name,
            "paddocks": parsed["paddocks"],
            "paddock_count": len(parsed["paddocks"]),
            "existing_farm": existing_farm,
            "created_count": created_count,
            "updated_count": updated_count,
            "retired_count": retired_count,
            "water_asset_count": len(parsed.get("water_assets", [])),
            "water_created_count": water_created_count,
            "water_updated_count": water_updated_count,
            "water_archived_count": water_archived_count,
            "map_path": str(map_path),
        }
