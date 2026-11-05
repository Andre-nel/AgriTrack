package com.agritrack.mobile.data

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class WeirLevelsTest {
    @Test
    fun percentageCommandsRetainPrecisionOverflowAndUnknown() {
        val fractional = MobileCommand.waterAssetStatus("farm", "weir", "operational", "37.25", percentageLevel = true).payload
        assertEquals(37.25, fractional.getDouble("water_level_percent"), 0.0)
        assertFalse(fractional.getBoolean("is_overflowing"))
        assertFalse(fractional.has("water_level"))
        val overflow = MobileCommand.waterAssetStatus("farm", "weir", "operational", "overflowing", percentageLevel = true).payload
        assertEquals(100.0, overflow.getDouble("water_level_percent"), 0.0)
        assertTrue(overflow.getBoolean("is_overflowing"))
        assertTrue(MobileCommand.waterAssetStatus("farm", "weir", "operational", "", percentageLevel = true).payload.isNull("water_level_percent"))
        for (invalid in listOf("-1", "101", "NaN", "Infinity", "20.001")) {
            assertThrows(IllegalArgumentException::class.java) {
                MobileCommand.waterAssetStatus("farm", "weir", "operational", invalid, percentageLevel = true)
            }
        }
        assertEquals("half", MobileCommand.waterAssetStatus("farm", "tank", "operational", "half").payload.getString("water_level"))
    }

    @Test
    fun queuedWeirLevelClearsStaleVolumeOnAssetAndMap() {
        val props = JSONObject().put("id", "weir").put("feature_type", "water_asset")
            .put("estimated_volume_m3", 200).put("water_level", "50")
        val json = JSONObject().put("farm", JSONObject().put("id", "farm").put("name", "Farm"))
            .put("water_assets", JSONArray().put(JSONObject().put("id", "weir").put("name", "Weir")
                .put("asset_type", "weir").put("water_level", "50").put("water_level_percent", 50)
                .put("capacity_m3", 800).put("estimated_volume_m3", 200)))
            .put("map_features", JSONArray().put(JSONObject().put("type", "Feature")
                .put("geometry", JSONObject().put("type", "Polygon").put("coordinates", JSONArray()))
                .put("properties", props)))
        val command = MobileCommand.waterAssetStatus("farm", "weir", "operational", "37.25", percentageLevel = true)
        val snapshot = FarmSnapshot.fromJson(json).withOptimisticCommands(JSONArray().put(command.toJson()))
        val weir = snapshot.waterAssets.single()
        assertEquals(37.25, weir.waterLevelPercent!!, 0.0)
        assertEquals("37.25%", weir.waterLevelLabel)
        assertEquals(800.0, weir.capacityM3!!, 0.0)
        assertNull(weir.estimatedVolumeM3)
        assertEquals("pending_sync", weir.volumeEstimateStatus)
        val feature = JSONObject(snapshot.mapFeatures.single().propertiesJson)
        assertTrue(feature.isNull("estimated_volume_m3"))
        assertEquals("37.25%", feature.getString("water_level_label"))

        val underflow = MobileCommand.waterAssetStatus("farm", "weir", "operational", "37.25", percentageLevel = true, underflowing = true)
        assertTrue(underflow.payload.getBoolean("is_underflowing"))
        assertEquals("damaged", underflow.payload.getString("status"))
        val damaged = FarmSnapshot.fromJson(json).withOptimisticCommands(JSONArray().put(underflow.toJson()))
        val reading = damaged.waterAssets.single()
        assertEquals("underflowing", reading.waterLevel)
        assertEquals("37.25", reading.editableLevel)
        assertEquals("Under flowing (37.25%)", reading.waterLevelLabel)
        assertEquals("damaged", reading.status)
        assertTrue(JSONObject(damaged.mapFeatures.single().propertiesJson).getBoolean("is_underflowing"))
    }

    @Test
    fun bootstrapControlsPercentageEditorCapability() {
        assertTrue(MobileFormOptions.fromJson(JSONObject().put("percentage_water_level_types", JSONArray().put("weir"))).percentageWaterLevelTypes.contains("weir"))
        assertTrue(MobileFormOptions.fromJson(JSONObject()).percentageWaterLevelTypes.isEmpty())
    }
}
