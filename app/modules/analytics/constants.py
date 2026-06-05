from app.models.stock_ledger import StockEventType

ANALYTICS_GROUP_LABELS = {
    "species": "Species",
    "breed": "Breed",
    "sex": "Sex",
    "age_class": "Age Class",
    "farm": "Farm",
}
ANALYTICS_GROUP_ORDER = ("species", "breed", "sex", "age_class", "farm")
ANALYTICS_DEFAULT_GROUP_BY = ("species",)
ANALYTICS_STOCK_IN_TYPES = {
    StockEventType.birth,
    StockEventType.purchase,
    StockEventType.transfer_in,
    StockEventType.adjustment_in,
}
ANALYTICS_STOCK_OUT_TYPES = {
    StockEventType.death,
    StockEventType.sale,
    StockEventType.missing,
    StockEventType.transfer_out,
    StockEventType.adjustment_out,
}
LSU_PADDOCK_TRACKING_METRIC_LABELS = {
    "lsu_per_ha": "LSU/ha",
    "current_lsu": "LSU",
    "head_count": "Head Count",
}
LSU_PADDOCK_TRACKING_METRIC_AXIS_LABELS = {
    "lsu_per_ha": "LSU/ha",
    "current_lsu": "LSU",
    "head_count": "Head Count",
}
LSU_PADDOCK_TRACKING_METRIC_ORDER = ("lsu_per_ha", "current_lsu", "head_count")
LSU_PADDOCK_TRACKING_DEFAULT_METRIC = "lsu_per_ha"
LSU_PADDOCK_TRACKING_PLOT_MODES = {
    "overlay": "Overlay Selected Paddocks",
    "paddock": "Separate Plot Per Paddock",
}
WATER_ASSET_STATE_FIELD_LABELS = {
    "active": "Active State",
    "status": "Status",
    "water_level": "Water Level",
}
WATER_ASSET_STATE_FIELD_ORDER = ("active", "status", "water_level")
WATER_ASSET_STATE_DEFAULT_FIELDS = WATER_ASSET_STATE_FIELD_ORDER
WATER_ASSET_ANALYTICS_PLOT_MODES = {
    "overlay": "Overlay Selected Assets",
    "asset": "Separate Plot Per Asset",
}
WATER_ASSET_CURRENT_ACTIVE_FILTERS = {
    "all": "All Assets",
    "active": "Active Assets",
    "inactive": "Inactive Assets",
}
