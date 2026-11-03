import copy
import json
import xml.etree.ElementTree as ET
from math import cos, degrees, hypot, radians
from pathlib import Path

import pytest

from app.services import weir_volume_service as volume
from app.services.polygon_geometry import EARTH_RADIUS_M


def geographic(point):
    x, y = point
    return [25 + degrees(x / (EARTH_RADIUS_M * cos(radians(-32)))),
            -32 + degrees(y / EARTH_RADIUS_M)]


def polygon(outline, holes=()):
    return {"type": "Polygon", "coordinates": [
        [geographic(p) for p in [*ring, ring[0]]] for ring in [outline, *holes]
    ]}


DIAMOND = [(0, 0), (50, -10), (100, 0), (50, 10)]
RECTANGLE = [(0, 0), (100, 0), (100, 10), (0, 10)]


def assert_valid(outline, result):
    assert result["message"] is None, result
    line = result["centerline"]
    assert line is not None
    assert 2 <= len(line["coordinates"]) <= 500
    assert line["coordinates"][0] == pytest.approx(result["wall"], abs=1e-10)
    assert volume.validate_centerline(outline, line)


@pytest.mark.parametrize("hint, expected", [((-10, 4), (0, 4)), ((8, 4), (8, 0)), ((-2, -3), (0, 0)), ((0, 6), (0, 6))])
def test_wall_is_nearest_point_along_edge_or_vertex(hint, expected):
    outline = polygon(RECTANGLE)
    result = volume.default_geometry(outline, geographic(hint))
    assert result["wall"] == pytest.approx(geographic(expected), abs=1e-9)
    assert_valid(outline, result)


def test_symmetric_opposing_banks_have_straight_middle_and_farthest_endpoint():
    outline = polygon(DIAMOND)
    result = volume.default_geometry(outline, geographic((-5, 0)))
    assert_valid(outline, result)
    coordinates = result["centerline"]["coordinates"]
    assert len(coordinates) == 2
    assert coordinates[0] == pytest.approx(geographic((0, 0)))
    assert coordinates[-1] == pytest.approx(geographic((100, 0)))


def test_rectangular_end_uses_farthest_corner_not_edge_midpoint():
    outline = polygon(RECTANGLE)
    result = volume.default_geometry(outline, geographic((-1, 4)))
    assert_valid(outline, result)
    assert result["centerline"]["coordinates"][-1] == pytest.approx(geographic((100, 10)))
    # Away from either end, the draft stays midway between the horizontal banks.
    project, _ = volume._frame(outline["coordinates"][0])
    points = [project(p) for p in result["centerline"]["coordinates"]]
    crossings = [a[1] + (50 - a[0]) / (b[0] - a[0]) * (b[1] - a[1])
                 for a, b in zip(points, points[1:]) if a[0] < 50 < b[0]]
    assert crossings == pytest.approx([5], abs=0.3)


def test_winding_start_and_uneven_vertex_density_do_not_change_default():
    outline = polygon(DIAMOND)
    hint = geographic((-5, 0))
    expected = volume.default_geometry(outline, hint)
    reversed_ring = list(reversed(outline["coordinates"][0][:-1]))
    rotated = reversed_ring[2:] + reversed_ring[:2]
    reversed_outline = {"type": "Polygon", "coordinates": [[*rotated, rotated[0]]]}
    assert volume.default_geometry(reversed_outline, hint) == expected
    dense = polygon([(0, 0), (5, -1), (10, -2), (25, -5), (50, -10), (100, 0), (50, 10)])
    actual = volume.default_geometry(dense, hint)
    assert_valid(dense, actual)
    for a, b in zip(actual["centerline"]["coordinates"], expected["centerline"]["coordinates"], strict=True):
        assert a == pytest.approx(b, abs=1e-9)


def test_bent_outline_follows_channel():
    outline = polygon([(0, 0), (60, 0), (60, 40), (100, 40), (100, 50), (50, 50), (50, 10), (0, 10)])
    result = volume.default_geometry(outline, geographic((-1, 5)))
    assert_valid(outline, result)
    project, _ = volume._frame(outline["coordinates"][0])
    points = [project(p) for p in result["centerline"]["coordinates"]]
    crossings = [a[0] + (25 - a[1]) / (b[1] - a[1]) * (b[0] - a[0])
                 for a, b in zip(points, points[1:]) if a[1] < 25 < b[1]]
    assert crossings == pytest.approx([55], abs=0.3)
    assert points[-1] == pytest.approx((100, 50), abs=1e-6)


def test_real_curved_weir_can_preview_default_without_changing_inputs():
    ns = {"k": "http://www.opengis.net/kml/2.2"}
    root = ET.parse(Path(__file__).parents[1] / "fixtures/rietfontein_weir.kml").getroot()
    def coordinates(text):
        return [list(map(float, item.split(",")[:2])) for item in text.split()]
    outline = {"type": "Polygon", "coordinates": [coordinates(root.find(".//k:LinearRing/k:coordinates", ns).text)]}
    hint = coordinates(root.find(".//k:Point/k:coordinates", ns).text)[0]
    original = copy.deepcopy(outline)
    result = volume.default_geometry(outline, hint)
    assert_valid(outline, result)
    assert outline == original
    project, _ = volume._frame(outline["coordinates"][0])
    wall = project(result["wall"])
    def distance(point):
        x, y = project(point)
        return hypot(x - wall[0], y - wall[1])
    assert distance(result["centerline"]["coordinates"][-1]) == pytest.approx(max(map(distance, outline["coordinates"][0])))
    # Exercise the actual volume integration, not only topology validation.
    prepared = volume._prepare(
        json.dumps(outline), json.dumps(result["centerline"]), 1,
    )
    assert 0 < prepared.capacity_m3 < prepared.area_m2


def test_hole_crossing_keeps_wall_but_requires_manual_line():
    outline = polygon(DIAMOND, holes=[[(45, -2), (55, -2), (55, 2), (45, 2)]])
    result = volume.default_geometry(outline, geographic((-5, 0)))
    assert result["wall"] == pytest.approx(geographic((0, 0)))
    assert result["centerline"] is None
    assert "manual tracing" in result["message"]


def test_hole_away_from_middle_does_not_prevent_suggestion():
    outline = polygon(RECTANGLE, holes=[[(40, 1), (45, 1), (45, 2), (40, 2)]])
    assert_valid(outline, volume.default_geometry(outline, geographic((-5, 4))))


def test_missing_point_does_not_invent_wall():
    result = volume.default_geometry(polygon(DIAMOND))
    assert result["wall"] is result["centerline"] is None
    assert "No weir point" in result["message"]


@pytest.mark.parametrize("outline, hint", [
    ({"type": "Polygon", "coordinates": []}, geographic((0, 0))),
    (polygon([(0, 0), (10, 10), (0, 10), (10, 0)]), geographic((0, 0))),
    (polygon(DIAMOND), [float("nan"), 0]),
])
def test_invalid_inputs_explain_unavailable_default(outline, hint):
    result = volume.default_geometry(outline, hint)
    assert result["centerline"] is None
    assert "Unable to suggest geometry" in result["message"]
