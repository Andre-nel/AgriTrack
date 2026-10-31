"""Small explicit validators shared by orchard HTTP and import workflows."""

import math
from datetime import date


def label(value, field="Name", limit=120):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValueError(f"{field} is required and must be at most {limit} characters.")
    return value.strip()


def number(value, field, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number.")
    if not math.isfinite(value) or abs(value) > 1_000_000 or (positive and value <= 0):
        raise ValueError(f"{field} must be finite, within 1,000,000 metres" + (", and positive." if positive else "."))
    return float(value)


def integer(value, field, *, maximum=1000):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{field} must be a whole number between 1 and {maximum}.")
    return value


def choice(value, options, field):
    if value not in options:
        raise ValueError(f"{field} must be one of: {', '.join(options)}.")
    return value


def iso_date(value, field, *, required=False):
    if value in (None, "") and not required:
        return None
    try:
        if not isinstance(value, str) or len(value) != 10:
            raise ValueError
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{field} must be a date in YYYY-MM-DD format.") from None


def vertices(value):
    if not isinstance(value, list) or not 2 <= len(value) <= 200:
        raise ValueError("A line needs between 2 and 200 vertices.")
    points = []
    for point in value:
        if not isinstance(point, list) or len(point) != 2:
            raise ValueError("Each vertex must contain [x, y] metres.")
        xy = [number(point[0], "Vertex x"), number(point[1], "Vertex y")]
        if points and xy == points[-1]:
            raise ValueError("Adjacent line vertices must be different.")
        points.append(xy)
    return points


def project(point, points):
    """Nearest point on the polyline; ties use the first segment, also in JS."""
    best, distance = None, float("inf")
    for a, b in zip(points, points[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        fraction = max(0, min(1, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / (dx * dx + dy * dy)))
        candidate = [a[0] + fraction * dx, a[1] + fraction * dy]
        squared = (candidate[0] - point[0]) ** 2 + (candidate[1] - point[1]) ** 2
        if squared < distance:
            best, distance = candidate, squared
    return best
