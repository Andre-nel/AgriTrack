"""API schemas placeholder for future Marshmallow/Pydantic models."""


class FarmSchema:
    fields = ("id", "name", "timezone", "active")


class MobSchema:
    fields = ("id", "farm_id", "name", "status")


class PaddockSchema:
    fields = ("id", "farm_id", "name", "area_ha", "grazeable_area_ha", "status")
