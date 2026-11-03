"""Storage estimates for a traced, linear longitudinal bed with vertical banks.

This is an assumed bed, not bathymetry. Nearest-centreline stationing defines
depth through bends. Polygon holes are excluded by signed integration so their
area does not contribute to stored volume.
"""

import json
from bisect import bisect_right
from dataclasses import dataclass
from functools import lru_cache
from math import cos, hypot, isfinite, radians

from app.services.polygon_geometry import EARTH_RADIUS_M, PlanarGeometry

MODEL_VERSION = "linear-centreline-vertical-banks-v1"
EPS = 1e-7  # metres in the local frame
CHECK_LEVELS = (0.0001, 0.001, 0.01, 0.05, 0.25, 0.5, 0.75, 0.99, 1.0)
MAX_TRIANGLES = 200_000


class ConvergenceError(ValueError):
    pass


@dataclass(frozen=True)
class PreparedGeometry:
    """Cached integration data and the matching surface for editor previews.

    Surface triangles contain local (x metres, y metres, upstream fraction).
    The geographic exterior also supplies the projection origin. Only positive
    exterior triangles are retained; the viewer excludes polygon holes by clip.
    """

    facets: tuple
    verification: tuple
    capacity_m3: float
    area_m2: float
    exterior: tuple
    surface: tuple


def _coordinate(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("Geometry coordinates must contain longitude and latitude")
    if any(isinstance(v, bool) for v in value):
        raise ValueError("Invalid geometry coordinate")
    try:
        lon, lat = map(float, value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid geometry coordinate") from exc
    if not isfinite(lon) or not isfinite(lat) or not -180 <= lon <= 180 or not -90 <= lat <= 90:
        raise ValueError("Geometry coordinates are outside longitude/latitude ranges")
    return [lon, lat]


def _frame(ring):
    lon0, lat0 = ring[0]
    scale_x = EARTH_RADIUS_M * cos(radians(lat0))
    if abs(scale_x) < 1:
        raise ValueError("Weir geometry cannot be projected at the pole")

    def project(point):
        return (scale_x * radians(point[0] - lon0), EARTH_RADIUS_M * radians(point[1] - lat0))

    def unproject(point):
        from math import degrees
        return [lon0 + degrees(point[0] / scale_x), lat0 + degrees(point[1] / EARTH_RADIUS_M)]

    return project, unproject


def _edges(ring):
    return list(zip(ring, ring[1:] + ring[:1]))


def _closest(point, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    fraction = max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length2))
    projected = (a[0] + fraction * dx, a[1] + fraction * dy)
    return hypot(point[0] - projected[0], point[1] - projected[1]), projected, fraction


def _on_segment(p, a, b):
    return _closest(p, a, b)[0] <= EPS


def _intersects(a, b, c, d):
    cross = PlanarGeometry._cross_product
    x, y, z, w = cross(a, b, c), cross(a, b, d), cross(c, d, a), cross(c, d, b)
    if ((x > EPS and y < -EPS) or (x < -EPS and y > EPS)) and ((z > EPS and w < -EPS) or (z < -EPS and w > EPS)):
        return True
    return any(_on_segment(p, q, r) for p, q, r in ((a, c, d), (b, c, d), (c, a, b), (d, a, b)))


def _inside(point, ring, *, boundary=True):
    inside = False
    x, y = point
    for a, b in _edges(ring):
        if _on_segment(point, a, b):
            return boundary
        if (a[1] > y) != (b[1] > y) and x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]:
            inside = not inside
    return inside


def _validate_ring(ring):
    if len(ring) < 3 or abs(PlanarGeometry._signed_area(ring)) < 1e-6:
        raise ValueError("Weir polygon has zero area or too few vertices")
    edges = _edges(ring)
    if any(hypot(b[0] - a[0], b[1] - a[1]) <= EPS for a, b in edges):
        raise ValueError("Weir polygon contains repeated consecutive vertices")
    for i, (a, b) in enumerate(edges):
        # Adjacent collinear reversals overlap even though their common endpoint is allowed.
        prev = ring[i - 1]
        if abs(PlanarGeometry._cross_product(prev, a, b)) < EPS and (
            (prev[0] - a[0]) * (b[0] - a[0]) + (prev[1] - a[1]) * (b[1] - a[1]) > 0
        ):
            raise ValueError("Weir polygon contains overlapping edges")
        for j in range(i + 1, len(edges)):
            if j == i + 1 or (i == 0 and j == len(edges) - 1):
                continue
            if _intersects(a, b, *edges[j]):
                raise ValueError("Weir polygon intersects itself")


def validate_polygon(value):
    if not isinstance(value, dict) or value.get("type") != "Polygon":
        raise ValueError("A weir requires one connected GeoJSON Polygon")
    rings = value.get("coordinates")
    if not isinstance(rings, list) or not rings or any(not isinstance(r, list) for r in rings):
        raise ValueError("Weir polygon has no boundary")
    if sum(map(len, rings)) > 2000:
        raise ValueError("Weir polygon supports up to 2000 boundary vertices")
    normalized = []
    for ring in rings:
        coords = [_coordinate(p) for p in ring]
        if len(coords) < 4 or coords[0] != coords[-1]:
            raise ValueError("Weir polygon rings must be closed with at least three vertices")
        normalized.append(coords)
    project, _ = _frame(normalized[0])
    projected = [[project(p) for p in ring[:-1]] for ring in normalized]
    for ring in projected:
        _validate_ring(ring)
    for i, hole in enumerate(projected[1:], 1):
        if not _inside(hole[0], projected[0], boundary=False):
            raise ValueError("Weir polygon hole must lie inside the outer boundary")
        for other in projected[:i]:
            if any(_intersects(a, b, c, d) for a, b in _edges(hole) for c, d in _edges(other)):
                raise ValueError("Weir polygon boundaries must not touch or intersect")
        for other in projected[1:i]:
            if _inside(hole[0], other) or _inside(other[0], hole):
                raise ValueError("Weir polygon holes must not overlap")
    # Starting vertex, winding and hole order do not constitute a changed outline.
    canonical = []
    for index, ring in enumerate(normalized):
        open_ring = ring[:-1]
        if (PlanarGeometry._signed_area(projected[index]) > 0) != (index == 0):
            open_ring = list(reversed(open_ring))
        first = min(range(len(open_ring)), key=lambda i: tuple(open_ring[i]))
        ordered = open_ring[first:] + open_ring[:first]
        canonical.append(ordered + [ordered[0]])
    return {"type": "Polygon", "coordinates": [canonical[0], *sorted(canonical[1:], key=lambda r: json.dumps(r))]}


def validate_centerline(polygon, value):
    if not isinstance(value, dict) or value.get("type") != "LineString":
        raise ValueError("Trace a centreline from the wall to the upstream boundary")
    coords = value.get("coordinates")
    if not isinstance(coords, list) or not 2 <= len(coords) <= 500:
        raise ValueError("Centreline requires between 2 and 500 points")
    coords = [_coordinate(p) for p in coords]
    project, unproject = _frame(polygon["coordinates"][0])
    rings = [[project(p) for p in ring[:-1]] for ring in polygon["coordinates"]]
    line = [project(p) for p in coords]
    for index in (0, len(line) - 1):
        distance, nearest, _ = min((_closest(line[index], a, b) for a, b in _edges(rings[0])), key=lambda item: item[0])
        if distance > 1.0:
            raise ValueError("Centreline must start at the wall boundary and end at the upstream boundary")
        line[index] = nearest
        coords[index] = unproject(nearest)
    segments = list(zip(line, line[1:]))
    if any(hypot(a[0] - b[0], a[1] - b[1]) < 0.001 for a, b in segments):
        raise ValueError("Centreline contains repeated points")
    for i, (a, b) in enumerate(segments):
        for c, d in segments[i + 2:]:
            if _intersects(a, b, c, d):
                raise ValueError("Centreline must not intersect itself")
        if i and abs(PlanarGeometry._cross_product(line[i - 1], a, b)) < EPS and (
            (line[i - 1][0] - a[0]) * (b[0] - a[0]) + (line[i - 1][1] - a[1]) * (b[1] - a[1]) > 0
        ):
            raise ValueError("Centreline must not double back on itself")
        midpoint = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        if not _inside(midpoint, rings[0], boundary=False) or any(_inside(midpoint, hole) for hole in rings[1:]):
            raise ValueError("Centreline must remain inside the water polygon and outside its holes")
        for ring in rings:
            for c, d in _edges(ring):
                if not _intersects(a, b, c, d):
                    continue
                allowed_start = i == 0 and _on_segment(a, c, d)
                allowed_end = i == len(segments) - 1 and _on_segment(b, c, d)
                if not (allowed_start or allowed_end):
                    raise ValueError("Centreline crosses or touches a bank away from its endpoints")
    return {"type": "LineString", "coordinates": coords}


def _sample_bank(bank, intervals=128):
    """Bound the matching work and avoid bias from uneven outline vertex spacing."""
    distances = [0.0]
    for a, b in zip(bank, bank[1:]):
        distances.append(distances[-1] + hypot(b[0] - a[0], b[1] - a[1]))
    sampled = [bank[0]]
    for step in range(1, intervals):
        distance = distances[-1] * step / intervals
        index = bisect_right(distances, distance) - 1
        fraction = (distance - distances[index]) / (distances[index + 1] - distances[index])
        a, b = bank[index:index + 2]
        sampled.append(tuple(a[k] + fraction * (b[k] - a[k]) for k in (0, 1)))
    return [*sampled, bank[-1]]


def _bank_midpoints(first, second):
    """Approximate the middle of two banks, keeping wall-to-upstream order.

    Equal fractions of bank length provide initial midpoints. Different bank
    lengths at bends can put those outside the channel, so correct each point
    toward equal distance to the nearest segments on the two banks. The update
    is a Newton step for distance(first bank) - distance(second bank) = 0.
    Iterations are bounded; the complete line is validated before it is offered.
    """
    midpoints = [tuple((a[k] + b[k]) / 2 for k in (0, 1))
                 for a, b in zip(_sample_bank(first), _sample_bank(second))]
    banks = [list(zip(bank, bank[1:])) for bank in (first, second)]
    for index in range(1, len(midpoints) - 1):
        point = midpoints[index]
        for _ in range(12):
            distance_a, nearest_a, _ = min((_closest(point, a, b) for a, b in banks[0]), key=lambda item: item[0])
            distance_b, nearest_b, _ = min((_closest(point, a, b) for a, b in banks[1]), key=lambda item: item[0])
            if min(distance_a, distance_b) <= EPS or abs(distance_a - distance_b) < 0.01:
                break
            gradient = [(point[k] - nearest_a[k]) / distance_a - (point[k] - nearest_b[k]) / distance_b for k in (0, 1)]
            norm_squared = sum(value * value for value in gradient)
            if norm_squared <= EPS:
                break
            point = tuple(point[k] - (distance_a - distance_b) * gradient[k] / norm_squared for k in (0, 1))
        distances = [min(_closest(point, a, b)[0] for a, b in bank) for bank in banks]
        # Boundary samples at a split along a straight edge are trimmed below;
        # an interior point must actually reach the middle of the banks.
        if min(distances) > EPS and abs(distances[0] - distances[1]) > 0.01:
            raise ValueError("The opposing banks could not be matched; trace the centreline manually")
        midpoints[index] = point
    return midpoints


def _simplify_centerline(points, tolerance):
    """Remove redundant handles using a bounded Douglas–Peucker approximation."""
    if len(points) <= 2:
        return points
    closed = hypot(points[-1][0] - points[0][0], points[-1][1] - points[0][1]) <= EPS
    distance, index = max(
        (hypot(p[0] - points[0][0], p[1] - points[0][1]) if closed else _closest(p, points[0], points[-1])[0], i)
        for i, p in enumerate(points[1:-1], 1)
    )
    if distance <= tolerance:
        return [points[0], points[-1]]
    return _simplify_centerline(points[:index + 1], tolerance)[:-1] + _simplify_centerline(points[index:], tolerance)


def default_geometry(polygon, weir_point=None):
    """Suggest unsaved wall/centreline geometry for the editor, in local metres.

    Split the exterior at the nearest boundary point and the farthest vertex.
    Distance from a fixed point is maximized at a segment endpoint, so the latter
    is also the farthest point on the entire outline. Validate the draft through
    the same checks as a hand-drawn line; holes and difficult outlines may need
    manual tracing. This helper never updates an asset or chooses a wall without
    a location hint.
    """
    result = {"wall": None, "centerline": None, "message": None}
    if weir_point is None:
        result["message"] = "No weir point is available."
        return result
    try:
        polygon = validate_polygon(polygon)
        project, unproject = _frame(polygon["coordinates"][0])
        outline = [project(p) for p in polygon["coordinates"][0][:-1]]
        hint = project(_coordinate(weir_point))
        _, wall, edge = min(
            ((*_closest(hint, a, b)[:2], i) for i, (a, b) in enumerate(_edges(outline))),
            key=lambda item: item[0],
        )
        result["wall"] = unproject(wall)
        # Rotate around the snapped point, including it at both ends. When it
        # is already a vertex, avoid introducing a zero-length bank segment.
        ring = [wall, *outline[edge + 1:], *outline[:edge + 1], wall]
        ring = [p for i, p in enumerate(ring) if i == 0 or hypot(p[0] - ring[i - 1][0], p[1] - ring[i - 1][1]) > EPS]
        farthest = max(range(1, len(ring) - 1), key=lambda i: hypot(ring[i][0] - wall[0], ring[i][1] - wall[1]))
        points = _bank_midpoints(ring[:farthest + 1], list(reversed(ring[farthest:])))
        # At a split within a straight edge, early midpoints can lie on that
        # edge. Keep the exact endpoint and start the draft where it enters water.
        while len(points) > 2 and not _inside(points[1], outline, boundary=False):
            points.pop(1)
        while len(points) > 2 and not _inside(points[-2], outline, boundary=False):
            points.pop(-2)
        distinct = [points[0]]
        for point in points[1:-1]:
            if hypot(point[0] - distinct[-1][0], point[1] - distinct[-1][1]) >= 0.001:
                distinct.append(point)
        while len(distinct) > 1 and hypot(points[-1][0] - distinct[-1][0], points[-1][1] - distinct[-1][1]) < 0.001:
            distinct.pop()
        points = [*distinct, points[-1]]
        simplified = _simplify_centerline(points, 0.25)
        for candidate in (simplified, points):
            try:
                result["centerline"] = validate_centerline(polygon, {
                    "type": "LineString", "coordinates": [unproject(p) for p in candidate],
                })
                result["message"] = None
                return result
            except ValueError as exc:
                result["message"] = "The suggested centreline needs manual tracing: " + str(exc)
        return result
    except ValueError as exc:
        result["message"] = "Unable to suggest geometry: " + str(exc)
        return result


def _facet(triangle, sign=1):
    area = abs(PlanarGeometry._signed_area(triangle)) * sign
    return (area, *sorted(p[2] for p in triangle))


def _integral(facet, level):
    """Exact integral of max(0, level - station) over an affine triangle.

    Equivalent to clipping at the water plane and integrating the remaining
    prism. Repeated station values are handled by the all-wet/all-dry branches.
    """
    area, a, b, c = facet
    if level <= a:
        return 0.0
    linear = level - (a + b + c) / 3
    if level >= c:
        return area * linear
    if level < b:
        return area * (level - a) ** 3 / (3 * (b - a) * (c - a))
    return area * (linear + (c - level) ** 3 / (3 * (c - a) * (c - b)))


@lru_cache(maxsize=8)
def _prepare(polygon_json, centerline_json, height, version=MODEL_VERSION):
    polygon = validate_polygon(json.loads(polygon_json))
    centerline = validate_centerline(polygon, json.loads(centerline_json))
    project, _ = _frame(polygon["coordinates"][0])
    rings = [[project(p) for p in ring[:-1]] for ring in polygon["coordinates"]]
    path = [project(p) for p in centerline["coordinates"]]
    segments = []
    length = 0.0
    for a, b in zip(path, path[1:]):
        distance = hypot(b[0] - a[0], b[1] - a[1])
        segments.append((a, b, length, distance))
        length += distance

    @lru_cache(maxsize=200_000)
    def vertex(x, y):
        closest_distance, station = float("inf"), 0.0
        for a, b, start, distance in segments:
            separation, _, fraction = _closest((x, y), a, b)
            if separation < closest_distance:
                closest_distance, station = separation, start + distance * fraction
        return (x, y, station / length)

    def children(t):
        a, b, c = t
        ab = vertex((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        bc = vertex((b[0] + c[0]) / 2, (b[1] + c[1]) / 2)
        ca = vertex((c[0] + a[0]) / 2, (c[1] + a[1]) / 2)
        return ((a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca))

    triangles = []
    for i, ring in enumerate(rings):
        for t in PlanarGeometry._triangulate_ring(ring, "weir"):
            triangles.append((tuple(vertex(*p) for p in t), 1 if i == 0 else -1))
    area = sum(_facet(t, sign)[0] for t, sign in triangles)
    initial_capacity = sum(_integral(_facet(t, sign), 1) for t, sign in triangles) * height
    tolerance = max(0.025, abs(initial_capacity) * 0.00025) / height
    # A global error budget is essential at a bend: nearest-segment stationing
    # can jump across a bisector. Area-weighted refinement converges there;
    # demanding uniform pointwise continuity would never converge.
    import heapq
    mesh, verification, surface = [], [], []
    leaves, queue = {}, []
    serial, total_error = 0, 0.0

    def add(t, sign, depth=0):
        nonlocal serial, total_error
        serial += 1
        if serial > MAX_TRIANGLES:
            raise ConvergenceError("Geometry refinement exceeded its safe calculation limit")
        parts = children(t)
        facet = _facet(t)
        max_edge = max(hypot(a[0] - b[0], a[1] - b[1]) for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])))
        if max_edge > max(1.0, length / 32):
            if depth >= 16:
                raise ConvergenceError("Geometry is too large to refine safely")
            for child in parts:
                add(child, sign, depth + 1)
            return
        part_facets = [_facet(child) for child in parts]
        error = max(abs(_integral(facet, p) - sum(_integral(f, p) for f in part_facets)) for p in CHECK_LEVELS)
        a, b, c = t
        centroid = vertex((a[0] + b[0] + c[0]) / 3, (a[1] + b[1] + c[1]) / 3)
        station_error = abs(centroid[2] - (a[2] + b[2] + c[2]) / 3)
        for start, end, middle in ((a, b, parts[0][1]), (b, c, parts[1][2]), (c, a, parts[0][2])):
            station_error = max(station_error, abs(middle[2] - (start[2] + end[2]) / 2))
        error = max(error, abs(facet[0]) * station_error)
        leaves[serial] = (parts, sign, depth, error)
        total_error += error
        heapq.heappush(queue, (-error, serial))

    for triangle, sign in triangles:
        add(triangle, sign)
    while total_error > tolerance:
        _, key = heapq.heappop(queue)
        parts, sign, depth, error = leaves.pop(key)
        if depth >= 16:
            raise ConvergenceError("Geometry refinement did not converge; review the centreline through tight bends")
        total_error -= error
        for child in parts:
            add(child, sign, depth + 1)
    for parts, sign, _, _ in leaves.values():
        for child in parts:
            mesh.append(_facet(child, sign))
            if sign > 0:
                surface.append(child)
            verification.extend(_facet(grandchild, sign) for grandchild in children(child))
            if len(mesh) + len(verification) > MAX_TRIANGLES:
                raise ConvergenceError("Prepared geometry exceeded its safe calculation limit")
    capacity = sum(_integral(f, 1) for f in verification) * height
    if not isfinite(capacity) or capacity <= 0:
        raise ConvergenceError("The configured geometry produced no positive capacity")
    allowed_error = max(0.1, capacity * 0.001)
    for p in CHECK_LEVELS:
        if abs(sum(_integral(f, p) for f in mesh) - sum(_integral(f, p) for f in verification)) * height > allowed_error:
            raise ConvergenceError("Volume estimate did not meet the numerical convergence tolerance")
    return PreparedGeometry(
        tuple(mesh), tuple(verification), capacity, area,
        tuple(tuple(p) for p in polygon["coordinates"][0]), tuple(surface),
    )


def _water_fill(prepared, level):
    """Clip the volume model's surface triangles at the selected water plane.

    Return geographic polygons for a display layer, not a surveyed shoreline.
    The viewer clips them to the full outline (including its holes). Keeping the
    positive surface here avoids triangulation seams from separately refined
    negative hole triangles. Normal asset responses do not include this layer.
    """
    if level <= 0:
        return []
    if level >= 1:
        return [prepared.exterior[:-1]]
    _, unproject = _frame(prepared.exterior)
    polygons = []
    for triangle in prepared.surface:
        clipped = []
        previous = triangle[-1]
        previous_wet = previous[2] < level
        for current in triangle:
            current_wet = current[2] < level
            if current_wet != previous_wet:
                fraction = (level - previous[2]) / (current[2] - previous[2])
                clipped.append(tuple(previous[k] + fraction * (current[k] - previous[k]) for k in (0, 1)))
            if current_wet:
                clipped.append(current[:2])
            previous, previous_wet = current, current_wet
        clipped = PlanarGeometry._clean_projected_ring(clipped)
        if len(clipped) >= 3 and abs(PlanarGeometry._signed_area(clipped)) > 1e-9:
            polygons.append([unproject(point) for point in clipped])
    return polygons


def estimate(asset, *, include_water_fill=False):
    result = {"capacity_m3": None, "estimated_volume_m3": None,
              "weir_area_m2": None, "volume_model_version": MODEL_VERSION,
              "volume_estimate_status": "not_applicable", "volume_estimate_message": None}
    if include_water_fill:
        result["water_fill"] = None
    if asset.asset_type != "weir":
        return result
    if not asset.weir_polygon:
        result["volume_estimate_status"] = "missing_polygon"
        return result
    if asset.weir_geometry_review_required:
        result["volume_estimate_status"] = "review_required"
        return result
    if not asset.weir_centerline or not asset.weir_wall_height_m:
        result["volume_estimate_status"] = "setup_required"
        return result
    try:
        height = float(asset.weir_wall_height_m)
        if not isfinite(height) or height <= 0:
            raise ValueError("Wall height must be positive and finite")
        prepared = _prepare(json.dumps(asset.weir_polygon, sort_keys=True), json.dumps(asset.weir_centerline, sort_keys=True), height)
        mesh, check = prepared.facets, prepared.verification
        capacity, area = prepared.capacity_m3, prepared.area_m2
        volume = None
        if asset.water_level_percent is not None:
            p = float(asset.water_level_percent) / 100
            if not isfinite(p) or not 0 <= p <= 1:
                raise ValueError("Water percentage is outside the model range")
            volume = sum(_integral(f, p) for f in check) * height
            coarse = sum(_integral(f, p) for f in mesh) * height
            if abs(volume - coarse) > max(0.1, capacity * 0.001):
                raise ConvergenceError("Current-level volume did not meet the numerical convergence tolerance")
            volume = min(capacity, max(0.0, volume))
            if include_water_fill:
                result["water_fill"] = _water_fill(prepared, p)
        result.update(capacity_m3=round(capacity, 2), estimated_volume_m3=round(volume, 2) if volume is not None else None,
                      weir_area_m2=round(area, 2), volume_estimate_status="preliminary" if asset.weir_height_assumed else "ready")
    except ValueError as exc:
        result.update(volume_estimate_status="unavailable", volume_estimate_message=str(exc))
    return result
