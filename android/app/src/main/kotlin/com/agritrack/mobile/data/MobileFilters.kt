package com.agritrack.mobile.data

import java.time.LocalDate

data class PaddockFilterState(
    val name: String = "",
    val stockMin: String = "",
    val stockMax: String = "",
    val tag: String = "",
    val pressureMin: String = "",
    val pressureMax: String = "",
)

data class MobFilterState(
    val name: String = "",
    val countMin: String = "",
    val countMax: String = "",
    val species: Set<String> = emptySet(),
)

data class WaterFilterState(
    val name: String = "",
    val assetType: String = "",
    val status: String = "",
    val waterLevel: String = "",
)

data class CalendarFilterState(
    val startDate: String = "",
    val endDate: String = "",
    val itemType: String = "",
    val name: String = "",
    val stage: String = "",
    val assignee: String = "",
    val tag: String = "",
    val paddockId: String = "",
    val waterAssetId: String = "",
    val mobId: String = "",
)

fun filterPaddocks(snapshot: FarmSnapshot, filters: PaddockFilterState): List<PaddockSummary> {
    val stockByPaddock = snapshot.grazingByPaddock.associateBy { it.paddockId }
    val pressureByPaddock = snapshot.mapFeatures
        .filter { it.featureType == "paddock" && it.paddockId != null }
        .associate { it.paddockId.orEmpty() to it.grazingPressureRatio }
    val minStock = filters.stockMin.toNullableDouble()
    val maxStock = filters.stockMax.toNullableDouble()
    val minPressure = filters.pressureMin.toNullableDouble()
    val maxPressure = filters.pressureMax.toNullableDouble()

    return snapshot.paddocks.filter { paddock ->
        if (!paddock.name.containsQuery(filters.name)) return@filter false
        if (filters.tag.isNotBlank() && paddock.tags.none { it.containsQuery(filters.tag) }) return@filter false
        val stock = stockByPaddock[paddock.id]?.totalHead ?: 0.0
        if (minStock != null && stock < minStock) return@filter false
        if (maxStock != null && stock > maxStock) return@filter false
        if (minPressure != null || maxPressure != null) {
            val pressurePercent = pressureByPaddock[paddock.id]?.let { it * 100.0 } ?: return@filter false
            if (minPressure != null && pressurePercent < minPressure) return@filter false
            if (maxPressure != null && pressurePercent > maxPressure) return@filter false
        }
        true
    }
}

fun filterMobs(mobs: List<MobSummary>, filters: MobFilterState): List<MobSummary> {
    val species = filters.species.map { it.normalizedToken() }.filter { it.isNotBlank() }.toSet()
    val minCount = filters.countMin.toNullableDouble()
    val maxCount = filters.countMax.toNullableDouble()

    return mobs.filter { mob ->
        if (!mob.name.containsQuery(filters.name)) return@filter false
        if (minCount != null && mob.totalHead < minCount) return@filter false
        if (maxCount != null && mob.totalHead > maxCount) return@filter false
        if (species.isNotEmpty()) {
            val mobSpecies = mob.balances.map { it.animalGroupType.species.normalizedToken() }.toSet()
            if (mobSpecies.intersect(species).isEmpty()) return@filter false
        }
        true
    }
}

fun filterWaterAssets(assets: List<WaterAssetSummary>, filters: WaterFilterState): List<WaterAssetSummary> =
    assets.filter { asset ->
        asset.name.containsQuery(filters.name) &&
            asset.assetType.matchesOptionalToken(filters.assetType) &&
            asset.status.matchesOptionalWaterValue(filters.status) &&
            asset.waterLevel.matchesOptionalWaterValue(filters.waterLevel)
    }

fun filterCalendarItems(items: List<CalendarItemSummary>, filters: CalendarFilterState): List<CalendarItemSummary> {
    val startDate = filters.startDate.toNullableDate()
    val endDate = filters.endDate.toNullableDate()
    val taskOnlyFilterActive = filters.assignee.isNotBlank() ||
        filters.tag.isNotBlank() ||
        filters.paddockId.isNotBlank() ||
        filters.waterAssetId.isNotBlank() ||
        filters.mobId.isNotBlank()

    return items.filter { item ->
        val itemDate = item.date.toNullableDate()
        if (startDate != null && itemDate != null && itemDate < startDate) return@filter false
        if (endDate != null && itemDate != null && itemDate > endDate) return@filter false
        if (!item.kind.matchesOptionalToken(filters.itemType)) return@filter false
        if (!item.title.containsQuery(filters.name)) return@filter false
        if (filters.stage.isNotBlank() && !(item.stage ?: item.stageLabel.orEmpty()).containsQuery(filters.stage)) {
            return@filter false
        }
        if (taskOnlyFilterActive && item.kind != "task") return@filter false
        if (filters.assignee.isNotBlank() && !(item.assigneeName ?: "").containsQuery(filters.assignee)) {
            return@filter false
        }
        if (filters.tag.isNotBlank() && item.tags.none { it.containsQuery(filters.tag) }) return@filter false
        if (filters.paddockId.isNotBlank() && !item.hasEntity("paddock", filters.paddockId)) return@filter false
        if (filters.waterAssetId.isNotBlank() && !item.hasEntity("water_asset", filters.waterAssetId)) return@filter false
        if (filters.mobId.isNotBlank() && !item.hasEntity("mob", filters.mobId)) return@filter false
        true
    }
}

private fun CalendarItemSummary.hasEntity(entityType: String, entityId: String): Boolean =
    entityLinks.any { it.entityType == entityType && it.entityId == entityId }

private fun String.containsQuery(query: String): Boolean =
    query.isBlank() || normalizedToken().contains(query.normalizedToken())

private fun String.matchesOptionalToken(value: String): Boolean =
    value.isBlank() || normalizedToken() == value.normalizedToken()

private fun String?.matchesOptionalWaterValue(value: String): Boolean {
    if (value.isBlank()) return true
    val normalizedValue = value.normalizedToken()
    val normalizedActual = (this ?: "").normalizedToken()
    if (normalizedValue == "unknown") {
        return normalizedActual.isBlank() || normalizedActual == "unknown"
    }
    return normalizedActual == normalizedValue
}

private fun String.normalizedToken(): String = trim().lowercase()

private fun String.toNullableDouble(): Double? = trim().takeIf { it.isNotBlank() }?.toDoubleOrNull()

private fun String.toNullableDate(): LocalDate? = runCatching {
    trim().takeIf { it.isNotBlank() }?.let(LocalDate::parse)
}.getOrNull()
