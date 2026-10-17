import pytest

from app.modules.mobs.forms import (
    parse_count_move_allocations,
    parse_move_allocations,
    parse_split_rows,
    parse_transfer_rows,
)


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


def test_parse_move_allocations_rejects_duplicate_paddocks():
    with pytest.raises(ValueError, match="Duplicate paddock rows are not allowed"):
        parse_move_allocations(
            paddock_ids=["north", "north"],
            allocation_pcts=["50", "50"],
            valid_paddock_ids={"north"},
        )


def test_parse_move_allocations_validates_farm_per_row():
    assert parse_move_allocations(
        farm_ids=["farm-a", "farm-b"],
        paddock_ids=["north", "south"],
        allocation_pcts=["25", "75"],
        valid_farm_ids={"farm-a", "farm-b"},
        valid_paddock_farm_ids={"north": "farm-a", "south": "farm-b"},
    ) == [
        {"paddock_id": "north", "allocation_fraction": "0.25"},
        {"paddock_id": "south", "allocation_fraction": "0.75"},
    ]


def test_parse_move_allocations_rejects_mismatched_row_farm():
    with pytest.raises(ValueError, match="Selected paddock is invalid for the selected farm"):
        parse_move_allocations(
            farm_ids=["farm-a"],
            paddock_ids=["south"],
            allocation_pcts=["100"],
            valid_farm_ids={"farm-a", "farm-b"},
            valid_paddock_farm_ids={"south": "farm-b"},
        )


def test_parse_count_move_allocations_merges_duplicate_destination_paddocks():
    assert parse_count_move_allocations(
        paddock_ids=["north", "north", "south"],
        group_counts_by_id={
            "ewes": ["2", "3", "0"],
            "lambs": ["1", "0", "4"],
        },
        valid_paddock_ids={"north", "south"},
    ) == [
        {
            "paddock_id": "north",
            "group_counts": [
                {"animal_group_type_id": "ewes", "head_count": 5},
                {"animal_group_type_id": "lambs", "head_count": 1},
            ],
        },
        {
            "paddock_id": "south",
            "group_counts": [{"animal_group_type_id": "lambs", "head_count": 4}],
        },
    ]


def test_parse_count_move_allocations_accepts_one_animal_type_per_row():
    assert parse_count_move_allocations(
        farm_ids=["farm-a", "farm-a", "farm-b"],
        paddock_ids=["north", "north", "south"],
        group_ids=["ewes", "lambs", "lambs"],
        head_counts=["2", "1", "4"],
        valid_farm_ids={"farm-a", "farm-b"},
        valid_paddock_farm_ids={"north": "farm-a", "south": "farm-b"},
    ) == [
        {
            "paddock_id": "north",
            "group_counts": [
                {"animal_group_type_id": "ewes", "head_count": 2},
                {"animal_group_type_id": "lambs", "head_count": 1},
            ],
        },
        {
            "paddock_id": "south",
            "group_counts": [{"animal_group_type_id": "lambs", "head_count": 4}],
        },
    ]


def test_parse_count_move_allocations_requires_type_and_count_per_row():
    with pytest.raises(ValueError, match="requires an animal type"):
        parse_count_move_allocations(
            farm_ids=["farm-a"],
            paddock_ids=["north"],
            group_ids=[""],
            head_counts=["2"],
            valid_farm_ids={"farm-a"},
            valid_paddock_farm_ids={"north": "farm-a"},
        )

    with pytest.raises(ValueError, match="requires a count"):
        parse_count_move_allocations(
            farm_ids=["farm-a"],
            paddock_ids=["north"],
            group_ids=["ewes"],
            head_counts=[""],
            valid_farm_ids={"farm-a"},
            valid_paddock_farm_ids={"north": "farm-a"},
        )


def test_parse_transfer_rows_preserves_destinations_and_notes_for_repeated_groups():
    assert parse_transfer_rows(
        farm_ids=[" farm-a ", "farm-b", "farm-a", ""],
        destination_mob_ids=[" north ", "south", "north", ""],
        group_ids=[" ewes ", "ewes", "ewes", ""],
        quantities=[" 3 ", "2", "1", ""],
        notes=[" First transfer ", "", "Separate note", ""],
    ) == [
        {
            "destination_farm_id": "farm-a", "destination_mob_id": "north",
            "animal_group_type_id": "ewes", "quantity": 3, "note": "First transfer",
        },
        {
            "destination_farm_id": "farm-b", "destination_mob_id": "south",
            "animal_group_type_id": "ewes", "quantity": 2, "note": None,
        },
        {
            "destination_farm_id": "farm-a", "destination_mob_id": "north",
            "animal_group_type_id": "ewes", "quantity": 1, "note": "Separate note",
        },
    ]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("farm_ids", "", "requires a destination farm"),
        ("destination_mob_ids", "", "requires a destination mob"),
        ("group_ids", "", "requires a group and quantity"),
        ("quantities", "", "requires a group and quantity"),
        ("quantities", "2.5", "Transfer quantities must be whole numbers"),
        ("quantities", "invalid", "Transfer quantities must be whole numbers"),
        ("quantities", "0", "Transfer quantities must be greater than 0"),
        ("quantities", "-1", "Transfer quantities must be greater than 0"),
    ],
)
def test_parse_transfer_rows_rejects_invalid_row(field, value, message):
    fields = {
        "farm_ids": ["farm-a"], "destination_mob_ids": ["north"],
        "group_ids": ["ewes"], "quantities": ["3"], "notes": [""],
    }
    fields[field] = [value]
    with pytest.raises(ValueError, match=message):
        parse_transfer_rows(**fields)


@pytest.mark.parametrize("field", ["farm_ids", "destination_mob_ids", "group_ids", "quantities", "notes"])
@pytest.mark.parametrize("length", [0, 1, 3])
def test_parse_transfer_rows_rejects_misaligned_fields(field, length):
    fields = {
        "farm_ids": ["farm-a", "farm-b"], "destination_mob_ids": ["north", "south"],
        "group_ids": ["ewes", "ewes"], "quantities": ["3", "2"], "notes": ["First", "Second"],
    }
    fields[field] = [fields[field][0]] * length
    with pytest.raises(ValueError, match="Transfer row fields do not match"):
        parse_transfer_rows(**fields)


@pytest.mark.parametrize("values", [[], [""], [" ", ""]])
def test_parse_transfer_rows_requires_a_nonempty_row(values):
    with pytest.raises(ValueError, match="Add at least one transfer row"):
        parse_transfer_rows(
            farm_ids=values, destination_mob_ids=values, group_ids=values,
            quantities=values, notes=values,
        )


def test_parse_transfer_rows_does_not_discard_a_note_without_a_destination():
    with pytest.raises(ValueError, match="requires a destination farm"):
        parse_transfer_rows(
            farm_ids=[""], destination_mob_ids=[""], group_ids=[""],
            quantities=[""], notes=["Unassigned note"],
        )


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
