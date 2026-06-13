package com.agritrack.mobile.data

import org.json.JSONArray
import org.json.JSONObject

private val supportedMapGeometryTypes = setOf(
    "Polygon",
    "MultiPolygon",
    "LineString",
    "MultiLineString",
    "Point",
)

internal fun mobileMapFeatureCollectionJson(features: List<MapFeatureSummary>): String {
    val serializedFeatures = JSONArray()
    features.forEach { feature ->
        val geometry = feature.geometryJson.asJsonObject() ?: return@forEach
        if (!geometry.isSupportedMapGeometry()) return@forEach

        serializedFeatures.put(
            JSONObject()
                .put("type", "Feature")
                .put("geometry", geometry)
                .put("properties", feature.propertiesJson.asJsonObject() ?: fallbackMapProperties(feature)),
        )
    }

    return JSONObject()
        .put("type", "FeatureCollection")
        .put("features", serializedFeatures)
        .toString()
}

private fun String.asJsonObject(): JSONObject? =
    runCatching { JSONObject(this) }.getOrNull()

private fun JSONObject.isSupportedMapGeometry(): Boolean =
    optString("type") in supportedMapGeometryTypes && has("coordinates") && !isNull("coordinates")

private fun fallbackMapProperties(feature: MapFeatureSummary): JSONObject {
    val properties = JSONObject()
        .put("feature_type", feature.featureType)
        .put("name", feature.name)
    feature.farmId?.let { properties.put("farm_id", it) }
    feature.farmName?.let { properties.put("farm_name", it) }
    feature.paddockId?.let { properties.put("paddock_id", it) }
    if (feature.featureType == "water_asset") {
        feature.waterAssetId?.let { properties.put("id", it) }
    }
    if (feature.featureType == "gate") {
        feature.gateId?.let { properties.put("gate_id", it) }
        feature.gateStatus?.let { properties.put("status", it) }
    }
    if (feature.featureType == "fence_section") {
        feature.fenceSectionId?.let {
            properties.put("id", it)
            properties.put("fence_section_id", it)
        }
        feature.fenceCondition?.let { properties.put("condition", it) }
    }
    feature.grazingPressureRatio?.let { properties.put("grazing_pressure_ratio", it) }
    feature.currentLsu?.let { properties.put("current_lsu", it) }
    return properties
}
