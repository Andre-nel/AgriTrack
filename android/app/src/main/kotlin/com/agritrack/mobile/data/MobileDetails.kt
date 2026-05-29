package com.agritrack.mobile.data

import java.time.Duration
import java.time.Instant
import java.time.OffsetDateTime

data class PaddockStockLine(
    val label: String,
    val head: Double,
)

data class PaddockWaterAvailability(
    val assets: List<WaterAssetSummary>,
    val alertLevel: String?,
    val alertMessage: String?,
)

data class MobGrazingPaddock(
    val paddockId: String,
    val paddockName: String,
    val allocationPct: Double,
    val startAt: String?,
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

fun mobGrazingPaddocks(snapshot: FarmSnapshot, mobId: String): List<MobGrazingPaddock> {
    val paddocksById = snapshot.paddocks.associateBy { it.id }
    val fromPaddockSummaries = snapshot.grazingByPaddock.flatMap { grazing ->
        grazing.mobs.filter { it.mobId == mobId }.map { mob ->
            MobGrazingPaddock(
                paddockId = grazing.paddockId,
                paddockName = paddocksById[grazing.paddockId]?.name ?: grazing.paddockId,
                allocationPct = mob.allocationPct,
                startAt = mob.startAt ?: mobPaddockGrazingStartAt(snapshot, mobId, grazing.paddockId),
            )
        }
    }
    if (fromPaddockSummaries.isNotEmpty()) return fromPaddockSummaries

    return snapshot.activeGrazing
        .filter { it.mobId == mobId }
        .flatMap { grazing ->
            grazing.allocations.map { allocation ->
                MobGrazingPaddock(
                    paddockId = allocation.paddockId,
                    paddockName = paddocksById[allocation.paddockId]?.name ?: allocation.paddockId,
                    allocationPct = allocation.allocationFraction * 100.0,
                    startAt = grazing.startAt,
                )
            }
        }
}

fun mobPaddockGrazingStartAt(snapshot: FarmSnapshot, mobId: String, paddockId: String): String? {
    var earliestText: String? = null
    var earliestInstant: Instant? = null
    snapshot.activeGrazing
        .filter { grazing ->
            grazing.mobId == mobId && grazing.allocations.any { allocation -> allocation.paddockId == paddockId }
        }
        .forEach { grazing ->
            val startAt = grazing.startAt ?: return@forEach
            val instant = parseGrazingInstant(startAt)
            if (instant == null) {
                if (earliestText == null) earliestText = startAt
            } else if (earliestInstant?.let { instant < it } != false) {
                earliestInstant = instant
                earliestText = startAt
            }
        }
    return earliestText
}

fun grazingDurationDays(startAt: String?, now: Instant = Instant.now()): Double? {
    val start = parseGrazingInstant(startAt) ?: return null
    val seconds = Duration.between(start, now).seconds.coerceAtLeast(0L)
    return seconds / 86400.0
}

private fun parseGrazingInstant(value: String?): Instant? {
    if (value.isNullOrBlank()) return null
    return runCatching { OffsetDateTime.parse(value).toInstant() }
        .getOrElse { runCatching { Instant.parse(value) }.getOrNull() }
}

fun servedPaddockNames(snapshot: FarmSnapshot, asset: WaterAssetSummary): List<String> {
    val paddocksById = snapshot.paddocks.associateBy { it.id }
    return asset.servedPaddockIds.map { paddockId ->
        paddocksById[paddockId]?.name ?: paddockId
    }
}
