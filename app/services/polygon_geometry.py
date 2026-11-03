"""Shared planar polygon primitives; coordinates must use a common metric frame."""

from math import cos, radians

EARTH_RADIUS_M = 6371008.8
GEOMETRY_EPSILON = 1e-9


class PlanarGeometry:
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

