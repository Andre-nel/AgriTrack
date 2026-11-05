"""Level conversion shared by recording, history, and presentation.

Weir percentages measure depth at the wall, never percentage of stored volume.
Categorical inputs remain accepted for older clients and queued observations.
"""

from decimal import Decimal, InvalidOperation

LEGACY_PERCENTAGES = {"empty": 0, "low": 25, "half": 50, "high": 75, "full": 100,
                      "overflowing": 100}


def parse_percent(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("water_level_percent must be a number between 0 and 100")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("water_level_percent must be a number between 0 and 100") from exc
    if not result.is_finite() or not 0 <= result <= 100:
        raise ValueError("water_level_percent must be between 0 and 100")
    if result != result.quantize(Decimal("0.01")):
        raise ValueError("water_level_percent supports at most two decimal places")
    return result


def parse_legacy_weir_level(value):
    text = str(value).strip().lower() if value is not None else ""
    if text == "unknown":
        return None, False
    if text in LEGACY_PERCENTAGES:
        return Decimal(LEGACY_PERCENTAGES[text]), text == "overflowing"
    return parse_percent(text), False


def _percent_text(percent):
    return format(Decimal(str(percent)).normalize(), "f")


def level_token(row, prefix=""):
    percent = getattr(row, prefix + "water_level_percent", None)
    if getattr(row, prefix + "is_underflowing", False):
        return "underflowing"
    overflowing = getattr(row, prefix + "is_overflowing", False)
    if overflowing:
        return "overflowing"
    if percent is not None:
        return _percent_text(percent)
    return getattr(row, prefix + "water_level", None)


def level_label(row, prefix=""):
    percent = getattr(row, prefix + "water_level_percent", None)
    if getattr(row, prefix + "is_underflowing", False):
        return "Under flowing" if percent is None else f"Under flowing ({_percent_text(percent)}%)"
    if getattr(row, prefix + "is_overflowing", False):
        return "Overflowing (100%)"
    if percent is not None:
        return level_token(row, prefix) + "%"
    return (getattr(row, prefix + "water_level", None) or "Unknown").replace("_", " ").title()
