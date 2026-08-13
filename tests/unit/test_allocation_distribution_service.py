from app.services.allocation_distribution_service import AllocationDistributionService


def test_split_count_uses_largest_remainder_with_deterministic_ties():
    assert AllocationDistributionService.split_count(
        5,
        [("b", 1), ("a", 1), ("c", 1)],
    ) == {"b": 2, "a": 2, "c": 1}


def test_split_count_falls_back_to_equal_weights_and_cleans_zero_totals():
    assert AllocationDistributionService.split_count(
        3,
        [("b", 0), ("a", 0)],
    ) == {"b": 1, "a": 2}
    assert AllocationDistributionService.split_count(
        0,
        [("a", 10), ("b", 20)],
    ) == {"a": 0, "b": 0}
