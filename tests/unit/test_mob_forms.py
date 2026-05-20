import pytest

from app.modules.mobs.forms import parse_move_allocations, parse_split_rows, parse_transfer_rows


def test_parse_move_allocations_builds_fraction_rows():
    assert parse_move_allocations(
        paddock_ids=["north", "south"],
        allocation_pcts=["25", "75"],
        valid_paddock_ids={"north", "south"},
    ) == [
        {"paddock_id": "north", "allocation_fraction": "0.25"},
        {"paddock_id": "south", "allocation_fraction": "0.75"},
    ]


def test_parse_move_allocations_requires_total_100():
    with pytest.raises(ValueError, match="Allocation percentages must add up to 100"):
        parse_move_allocations(
            paddock_ids=["north"],
            allocation_pcts=["50"],
            valid_paddock_ids={"north"},
        )


def test_parse_transfer_rows_combines_duplicate_groups():
    assert parse_transfer_rows(["ewes", "ewes", ""], ["3", "2", ""]) == [
        {"animal_group_type_id": "ewes", "quantity": 5}
    ]


def test_parse_transfer_rows_rejects_non_integer_quantities():
    with pytest.raises(ValueError, match="Transfer quantities must be whole numbers"):
        parse_transfer_rows(["ewes"], ["2.5"])


def test_parse_split_rows_groups_quantities_by_new_mob_name():
    assert parse_split_rows(
        names=["North A", "North A", "North B"],
        group_ids=["ewes", "lambs", "ewes"],
        quantities=["3", "4", "2"],
    ) == [
        {
            "name": "North A",
            "groups": [
                {"animal_group_type_id": "ewes", "quantity": 3},
                {"animal_group_type_id": "lambs", "quantity": 4},
            ],
        },
        {
            "name": "North B",
            "groups": [{"animal_group_type_id": "ewes", "quantity": 2}],
        },
    ]


def test_parse_split_rows_requires_at_least_one_complete_row():
    with pytest.raises(ValueError, match="Add at least one split allocation row"):
        parse_split_rows([""], [""], [""])
