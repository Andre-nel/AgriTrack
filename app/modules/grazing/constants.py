GRAZING_ANALYTICS_METRIC_LABELS = {
    "current_lsu": "Current LSU",
    "lsu_per_ha": "LSU/ha",
    "ha_per_lsu": "ha/LSU",
    "pressure_pct": "Pressure (%)",
}
GRAZING_ANALYTICS_METRIC_AXIS_LABELS = {
    "current_lsu": "LSU",
    "lsu_per_ha": "LSU/ha",
    "ha_per_lsu": "ha/LSU",
    "pressure_pct": "Pressure (%)",
}
GRAZING_ANALYTICS_METRIC_ORDER = ("current_lsu", "lsu_per_ha", "ha_per_lsu", "pressure_pct")
GRAZING_ANALYTICS_DEFAULT_METRICS = ("current_lsu",)
GRAZING_ANALYTICS_SPLIT_MODES = {"metric", "paddock"}
GRAZING_ANALYTICS_POINT_FILTER_OPERATORS = {
    "gt": "Greater Than",
    "lt": "Less Than",
}
GRAZING_ANALYTICS_POINT_FILTER_MATCH_MODES = {
    "has_any": "Has Matching Points",
    "has_none": "Has No Matching Points",
}
