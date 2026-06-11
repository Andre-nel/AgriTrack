package com.agritrack.mobile.data

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Test

class MobileMapGeoJsonTest {
    @Test
    fun featureCollectionPreservesGeometryAndProperties() {
        val json = JSONObject(
            mobileMapFeatureCollectionJson(
                listOf(
                    mapFeature(
                        geometry = JSONObject()
                            .put("type", "Polygon")
                            .put(
                                "coordinates",
                                JSONArray().put(
                                    JSONArray()
                                        .put(JSONArray().put(25.0).put(-32.0))
                                        .put(JSONArray().put(25.1).put(-32.0))
                                        .put(JSONArray().put(25.1).put(-32.1))
                                        .put(JSONArray().put(25.0).put(-32.0)),
                                ),
                            ),
                        properties = JSONObject()
                            .put("feature_type", "paddock")
                            .put("name", "North Camp")
                            .put("paddock_id", "paddock-1")
                            .put("grazing_pressure_ratio", 0.42),
                    ),
                ),
            ),
        )

        val feature = json.getJSONArray("features").getJSONObject(0)

        assertEquals("FeatureCollection", json.getString("type"))
        assertEquals("Polygon", feature.getJSONObject("geometry").getString("type"))
        assertEquals("North Camp", feature.getJSONObject("properties").getString("name"))
        assertEquals("paddock-1", feature.getJSONObject("properties").getString("paddock_id"))
        assertEquals(0.42, feature.getJSONObject("properties").getDouble("grazing_pressure_ratio"), 0.0)
    }

    @Test
    fun featureCollectionSkipsInvalidGeometry() {
        val json = JSONObject(
            mobileMapFeatureCollectionJson(
                listOf(
                    mapFeature(
                        geometry = JSONObject().put("type", "GeometryCollection"),
                        properties = JSONObject().put("feature_type", "paddock"),
                    ),
                    mapFeature(
                        geometry = JSONObject()
                            .put("type", "Point")
                            .put("coordinates", JSONArray().put(25.0).put(-32.0)),
                        properties = JSONObject()
                            .put("feature_type", "water_asset")
                            .put("id", "tank-1")
                            .put("name", "Header Tank"),
                    ),
                ),
            ),
        )

        val features = json.getJSONArray("features")

        assertEquals(1, features.length())
        assertEquals("tank-1", features.getJSONObject(0).getJSONObject("properties").getString("id"))
    }

    @Test
    fun featureCollectionFallsBackToSelectableProperties() {
        val json = JSONObject(
            mobileMapFeatureCollectionJson(
                listOf(
                    MapFeatureSummary(
                        featureType = "water_asset",
                        name = "Header Tank",
                        farmId = "farm-1",
                        farmName = "Demo Farm",
                        paddockId = null,
                        waterAssetId = "tank-1",
                        waterAlertLevel = null,
                        waterAlertMessage = null,
                        grazingPressureRatio = null,
                        currentLsu = null,
                        hectaresPerCurrentLsu = null,
                        mobs = emptyList(),
                        geometryType = "Point",
                        geometryJson = JSONObject()
                            .put("type", "Point")
                            .put("coordinates", JSONArray().put(25.0).put(-32.0))
                            .toString(),
                        propertiesJson = "{",
                    ),
                ),
            ),
        )

        val properties = json.getJSONArray("features").getJSONObject(0).getJSONObject("properties")

        assertEquals("water_asset", properties.getString("feature_type"))
        assertEquals("Header Tank", properties.getString("name"))
        assertEquals("tank-1", properties.getString("id"))
    }

    private fun mapFeature(geometry: JSONObject, properties: JSONObject): MapFeatureSummary =
        MapFeatureSummary(
            featureType = properties.optString("feature_type", "feature"),
            name = properties.optString("name", "Map feature"),
            farmId = properties.optString("farm_id").takeIf { it.isNotBlank() },
            farmName = properties.optString("farm_name").takeIf { it.isNotBlank() },
            paddockId = properties.optString("paddock_id").takeIf { it.isNotBlank() },
            waterAssetId = properties.optString("id").takeIf { it.isNotBlank() },
            waterAlertLevel = null,
            waterAlertMessage = null,
            grazingPressureRatio = properties.optDouble("grazing_pressure_ratio").takeIf { !it.isNaN() },
            currentLsu = null,
            hectaresPerCurrentLsu = null,
            mobs = emptyList(),
            geometryType = geometry.optString("type"),
            geometryJson = geometry.toString(),
            propertiesJson = properties.toString(),
        )
}
