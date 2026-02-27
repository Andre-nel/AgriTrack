from decimal import Decimal


class ValidationService:
    @staticmethod
    def validate_positive_int(value: int, field_name: str = "value") -> None:
        if int(value) <= 0:
            raise ValueError(f"{field_name} must be positive")

    @staticmethod
    def validate_allocations(allocations: list[dict]) -> None:
        if not allocations:
            raise ValueError("At least one allocation is required")

        total_fraction = sum(Decimal(str(item.get("allocation_fraction", 0))) for item in allocations)
        if total_fraction != Decimal("1"):
            raise ValueError("Allocation fractions must sum to 1.0")

    @staticmethod
    def require_keys(payload: dict, keys: set[str]) -> None:
        missing = [k for k in keys if k not in payload]
        if missing:
            raise ValueError(f"Missing required fields: {', '.join(missing)}")
