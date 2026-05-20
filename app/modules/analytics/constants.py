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
