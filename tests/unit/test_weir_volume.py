from math import cos, degrees, radians
from types import SimpleNamespace

import pytest

from app.services.polygon_geometry import EARTH_RADIUS_M
from app.services import weir_volume_service as volume


def geographic(points):
    return [[25 + degrees(x / (EARTH_RADIUS_M * cos(radians(-32)))),
             -32 + degrees(y / EARTH_RADIUS_M)] for x, y in points]


def polygon(points, holes=()):
    return {"type": "Polygon", "coordinates": [geographic([*ring, ring[0]]) for ring in [points, *holes]]}


def line(points):
    return {"type": "LineString", "coordinates": geographic(points)}


def asset(outline=None, path=None, height=1, percent=50):
    return SimpleNamespace(asset_type="weir", weir_polygon=outline or polygon([(0, 0), (100, 0), (100, 10), (0, 10)]),
                           weir_centerline=path or line([(0, 5), (100, 5)]), weir_wall_height_m=height,
                           weir_height_assumed=True, weir_geometry_review_required=False, water_level_percent=percent)


def test_rectangle_exact_depth_curve_and_unknown():
    weir = asset()
    for p in [0, 0.01, 1, 25, 37.5, 50, 75, 100]:
        weir.water_level_percent = p
        result = volume.estimate(weir)
        assert result["volume_estimate_status"] == "preliminary", result
        assert result["capacity_m3"] == pytest.approx(500, abs=0.02)
        assert result["estimated_volume_m3"] == pytest.approx(500 * (p / 100) ** 2, abs=0.02)
    weir.water_level_percent = None
    assert volume.estimate(weir)["estimated_volume_m3"] is None


def test_variable_width_and_height_scaling():
    # Width increases as w(s)=10+s/10. Integral at full is 2000/3 m³.
    weir = asset(polygon([(0, 0), (100, -5), (100, 15), (0, 10)]))
    result = volume.estimate(weir)
    assert result["capacity_m3"] == pytest.approx(2000 / 3, abs=0.02)
    weir.weir_wall_height_m = 2
    higher = volume.estimate(weir)
    assert higher["capacity_m3"] == pytest.approx(2 * result["capacity_m3"], abs=0.02)
    assert higher["estimated_volume_m3"] == pytest.approx(2 * result["estimated_volume_m3"], abs=0.02)


def test_hole_is_excluded():
    weir = asset(polygon([(0, 0), (100, 0), (100, 10), (0, 10)], holes=[[(20, 1), (30, 1), (30, 3), (20, 3)]]))
    result = volume.estimate(weir)
    assert result["capacity_m3"] == pytest.approx(485, abs=0.02)
    assert result["estimated_volume_m3"] == pytest.approx(120, abs=0.02)


def test_bent_concave_basin_monotonic():
    weir = asset(polygon([(0, 0), (50, 0), (50, 40), (40, 40), (40, 10), (0, 10)]), line([(0, 5), (45, 5), (45, 40)]))
    values = []
    for p in [0, 0.01, 10, 25, 50, 75, 100]:
        weir.water_level_percent = p
        result = volume.estimate(weir)
        assert result["volume_estimate_status"] == "preliminary", result
        values.append(result["estimated_volume_m3"])
    assert values == sorted(values)
    assert values[-1] == pytest.approx(400, abs=1)


@pytest.mark.parametrize("outline", [
    [(0, 0), (10, 10), (0, 10), (10, 0)],
    [(0, 0), (10, 0), (10, 0), (10, 10), (0, 10)],
])
def test_invalid_polygon(outline):
    with pytest.raises(ValueError):
        volume.validate_polygon(polygon(outline))


@pytest.mark.parametrize("path", [
    [(5, 5), (90, 5)],
    [(0, 5), (50, 20), (100, 5)],
    [(0, 5), (80, 8), (20, 8), (80, 2), (100, 5)],
    [(0, 5), (50, 5), (50, 5), (100, 5)],
    [(0, 5), (20, 5), (50, 5), (50, 5), (100, 5)],
])
def test_invalid_centerline(path):
    weir = asset()
    with pytest.raises(ValueError):
        volume.validate_centerline(weir.weir_polygon, line(path))


def test_review_and_missing_geometry_withhold_estimates():
    weir = asset()
    weir.weir_geometry_review_required = True
    assert volume.estimate(weir)["volume_estimate_status"] == "review_required"
    weir.weir_polygon = None
    assert volume.estimate(weir)["volume_estimate_status"] == "missing_polygon"


def test_refinement_limit_withholds_estimate(monkeypatch):
    volume._prepare.cache_clear()
    monkeypatch.setattr(volume, "MAX_TRIANGLES", 1)
    assert volume.estimate(asset())["volume_estimate_status"] == "unavailable"
    volume._prepare.cache_clear()


def test_winding_basin_converges_against_independent_midpoint_integration():
    # A two-bend, 10 m wide channel. A fine independent grid catches field or
    # triangle integration errors without reusing the preparation algorithm.
    outline = [(0, 0), (60, 0), (60, 40), (100, 40), (100, 50), (50, 50), (50, 10), (0, 10)]
    path = [(0, 5), (55, 5), (55, 45), (100, 45)]
    weir = asset(polygon(outline), line(path))
    reference = {p: 0.0 for p in (0.001, 0.25, 0.5, 1.0)}
    step = 0.2
    for ix in range(500):
        x = (ix + 0.5) * step
        for iy in range(250):
            y = (iy + 0.5) * step
            if not (y < 10 and x < 60 or 50 <= x < 60 and y < 50 or x >= 60 and y >= 40):
                continue
            projections = [(min(55, x), 5, min(55, x)),
                           (55, min(45, max(5, y)), 55 + min(40, max(0, y - 5))),
                           (max(55, x), 45, 95 + max(0, x - 55))]
            station = min(projections, key=lambda q: (x - q[0]) ** 2 + (y - q[1]) ** 2)[2] / 140
            for p in reference:
                reference[p] += max(0, p - station) * step * step
    for p, expected in reference.items():
        weir.water_level_percent = p * 100
        result = volume.estimate(weir)
        assert result["volume_estimate_status"] == "preliminary", result
        assert result["estimated_volume_m3"] == pytest.approx(expected, abs=max(0.1, result["capacity_m3"] * 0.001))


def test_invalid_holes_and_nonfinite_coordinates():
    outer = [(0, 0), (100, 0), (100, 10), (0, 10)]
    for hole in [[(20, 0), (30, 0), (30, 3), (20, 3)], [(20, 20), (30, 20), (30, 30), (20, 30)]]:
        with pytest.raises(ValueError):
            volume.validate_polygon(polygon(outer, holes=[hole]))
    weir = asset(polygon(outer, holes=[[(20, 4), (30, 4), (30, 6), (20, 6)]]))
    with pytest.raises(ValueError):
        volume.validate_centerline(weir.weir_polygon, weir.weir_centerline)
    weir.weir_polygon["coordinates"][0][1][0] = float("nan")
    with pytest.raises(ValueError):
        volume.validate_polygon(weir.weir_polygon)


@pytest.mark.parametrize("percent", [0, 0.01, 25, 37.5, 50, 100])
def test_water_fill_matches_depth_along_rectangle(percent):
    weir = asset(percent=percent)
    result = volume.estimate(weir, include_water_fill=True)
    assert result["water_fill"] is not None
    project, _ = volume._frame(weir.weir_polygon["coordinates"][0])
    pieces = [[project(p) for p in polygon] for polygon in result["water_fill"]]
    area = sum(abs(volume.PlanarGeometry._signed_area(piece)) for piece in pieces)
    assert area == pytest.approx(1000 * percent / 100, abs=0.001)
    assert all(-1e-6 <= x <= percent + 1e-6 for piece in pieces for x, _ in piece)
    if percent == 0:
        assert pieces == []
    if percent == 100:
        assert len(pieces) == 1


def test_water_fill_uses_depth_not_percentage_of_area_or_volume():
    # At 50% depth, the widening basin is wet for half its length. Its wet area
    # is 625 m² out of 1500 m², rather than half the full area.
    weir = asset(polygon([(0, 0), (100, -5), (100, 15), (0, 10)]))
    result = volume.estimate(weir, include_water_fill=True)
    project, _ = volume._frame(weir.weir_polygon["coordinates"][0])
    area = sum(abs(volume.PlanarGeometry._signed_area([project(p) for p in piece])) for piece in result["water_fill"])
    assert area == pytest.approx(625, abs=0.001)
    assert area != pytest.approx(result["weir_area_m2"] / 2)


def test_water_fill_follows_bent_centerline_and_reverses_with_wall():
    weir = asset(polygon([(0, 0), (50, 0), (50, 40), (40, 40), (40, 10), (0, 10)]),
                 line([(0, 5), (45, 5), (45, 40)]), percent=75)
    project, _ = volume._frame(weir.weir_polygon["coordinates"][0])
    def contains(result, point):
        return any(volume._inside(point, [project(p) for p in piece]) for piece in result["water_fill"])
    result = volume.estimate(weir, include_water_fill=True)
    assert contains(result, (10, 5))
    assert contains(result, (45, 15))
    assert not contains(result, (45, 30))
    weir.weir_centerline["coordinates"].reverse()
    weir.water_level_percent = 25
    reverse = volume.estimate(weir, include_water_fill=True)
    assert contains(reverse, (45, 30))
    assert not contains(reverse, (10, 5))


def test_water_fill_is_opt_in_and_unavailable_states_do_not_look_empty():
    weir = asset()
    assert "water_fill" not in volume.estimate(weir)
    weir.water_level_percent = None
    assert volume.estimate(weir, include_water_fill=True)["water_fill"] is None
    weir.water_level_percent = 50
    weir.weir_geometry_review_required = True
    assert volume.estimate(weir, include_water_fill=True)["water_fill"] is None
    weir.weir_geometry_review_required = False
    weir.weir_centerline = None
    assert volume.estimate(weir, include_water_fill=True)["water_fill"] is None
