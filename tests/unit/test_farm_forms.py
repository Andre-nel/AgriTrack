from app.modules.farms.forms import parse_mob_lines, parse_paddock_lines, parse_placements


def test_parse_paddock_lines_supports_area_and_grazeable_area():
    rows = parse_paddock_lines("North, 10.5, 9.25\nSouth, 4\n\n")

    assert rows == [
        {"name": "North", "area_ha": 10.5, "grazeable_area_ha": 9.25},
        {"name": "South", "area_ha": 4.0, "grazeable_area_ha": 4.0},
    ]


def test_parse_mob_lines_ignores_blank_lines():
    assert parse_mob_lines("Ewes\n\nLambs\n") == ["Ewes", "Lambs"]


def test_parse_placements_requires_mob_and_paddock():
    assert parse_placements("Ewes, North\nmissing-only\nLambs, South, extra\n") == [
        ("Ewes", "North"),
        ("Lambs", "South"),
    ]
