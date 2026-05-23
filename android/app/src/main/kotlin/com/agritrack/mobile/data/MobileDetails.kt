package com.agritrack.mobile.data

data class PaddockStockLine(
    val label: String,
    val head: Double,
)

data class PaddockWaterAvailability(
    val assets: List<WaterAssetSummary>,
    val alertLevel: String?,
    val alertMessage: String?,
)

fun paddockGrazing(snapshot: FarmSnapshot, paddockId: String): PaddockGrazingSummary? =
    snapshot.grazingByPaddock.firstOrNull { it.paddockId == paddockId }

fun paddockMapFeature(snapshot: FarmSnapshot, paddockId: String): MapFeatureSummary? =
    snapshot.mapFeatures.firstOrNull { it.featureType == "paddock" && it.paddockId == paddockId }
        ?: snapshot.mapFeatures.firstOrNull { it.paddockId == paddockId }

fun waterAssetsForPaddock(snapshot: FarmSnapshot, paddockId: String): List<WaterAssetSummary> =
    snapshot.waterAssets.filter { asset ->
        asset.locationPaddockId == paddockId || paddockId in asset.servedPaddockIds
    }

fun paddockWaterAvailability(snapshot: FarmSnapshot, paddockId: String): PaddockWaterAvailability {
    val feature = paddockMapFeature(snapshot, paddockId)
    return PaddockWaterAvailability(
        assets = waterAssetsForPaddock(snapshot, paddockId),
        alertLevel = feature?.waterAlertLevel,
        alertMessage = feature?.waterAlertMessage,
    )
}

fun paddockStockLines(grazing: PaddockGrazingSummary?): List<PaddockStockLine> {
    if (grazing == null) return emptyList()
    if (grazing.groupHeads.isNotEmpty()) {
        return grazing.groupHeads.map { group ->
            PaddockStockLine(
                label = group.animalGroupType.label,
                head = group.head,
            )
        }
    }
    return grazing.speciesHeads.map { species ->
        PaddockStockLine(
            label = species.species.ifBlank { "Stock" },
            head = species.head,
        )
    }
}

fun servedPaddockNames(snapshot: FarmSnapshot, asset: WaterAssetSummary): List<String> {
    val paddocksById = snapshot.paddocks.associateBy { it.id }
    return asset.servedPaddockIds.map { paddockId ->
        paddocksById[paddockId]?.name ?: paddockId
    }
}
