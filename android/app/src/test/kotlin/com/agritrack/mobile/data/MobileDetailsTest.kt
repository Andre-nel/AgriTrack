package com.agritrack.mobile.data

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Test

class MobileDetailsTest {
    @Test
    fun paddockDetailFindsPressureFeatureAndWaterAvailability() {
        val snapshot = detailSnapshot()

        val feature = paddockMapFeature(snapshot, "paddock-1")
        val water = paddockWaterAvailability(snapshot, "paddock-1")

        assertEquals(0.64, feature?.grazingPressureRatio ?: 0.0, 0.001)
        assertEquals("warning", water.alertLevel)
        assertEquals(listOf("Header Tank", "Shared Trough"), water.assets.map { it.name })
    }

    @Test
    fun paddockStockLinesUseAnimalGroupsBeforeSpeciesFallback() {
        val group = AnimalGroupTypeSummary("group-1", "Cattle", "Angus", "cow", "")
        val grazing = PaddockGrazingSummary(
            paddockId = "paddock-1",
            totalHead = 12.0,
            mobs = emptyList(),
            speciesHeads = listOf(SpeciesHeadSummary("Cattle", 20.0)),
            groupHeads = listOf(PaddockGroupHeadSummary("group-1", group, 12.0)),
        )

        val lines = paddockStockLines(grazing)

        assertEquals(listOf("Cattle Angus cow"), lines.map { it.label })
        assertEquals(listOf(12.0), lines.map { it.head })
    }

    @Test
    fun paddockStockLinesFallBackToSpeciesTotals() {
        val grazing = PaddockGrazingSummary(
            paddockId = "paddock-1",
            totalHead = 35.5,
            mobs = emptyList(),
            speciesHeads = listOf(SpeciesHeadSummary("Sheep", 35.5)),
            groupHeads = emptyList(),
        )

        val lines = paddockStockLines(grazing)

        assertEquals(listOf("Sheep"), lines.map { it.label })
        assertEquals(listOf(35.5), lines.map { it.head })
    }

    @Test
    fun servedPaddockNamesFallsBackToIds() {
        val snapshot = detailSnapshot()
        val asset = snapshot.waterAssets.first { it.name == "Shared Trough" }

        assertEquals(listOf("North Camp", "missing-paddock"), servedPaddockNames(snapshot, asset))
    }

    private fun detailSnapshot(): FarmSnapshot =
        FarmSnapshot.fromJson(
            JSONObject()
                .put(
                    "farm",
                    JSONObject()
                        .put("id", "farm-1")
                        .put("name", "Detail Farm")
                        .put("timezone", "UTC"),
                )
                .put(
                    "paddocks",
                    JSONArray()
                        .put(JSONObject().put("id", "paddock-1").put("name", "North Camp").put("status", "active"))
                        .put(JSONObject().put("id", "paddock-2").put("name", "South Camp").put("status", "resting")),
                )
                .put(
                    "water_assets",
                    JSONArray()
                        .put(
                            JSONObject()
                                .put("id", "tank-1")
                                .put("name", "Header Tank")
                                .put("asset_type", "tank")
                                .put("asset_type_label", "Tank")
                                .put("location_paddock_id", "paddock-1"),
                        )
                        .put(
                            JSONObject()
                                .put("id", "trough-1")
                                .put("name", "Shared Trough")
                                .put("asset_type", "trough")
                                .put("asset_type_label", "Trough")
                                .put("served_paddock_ids", JSONArray().put("paddock-1").put("missing-paddock")),
                        ),
                )
                .put(
                    "map_features",
                    JSONArray()
                        .put(
                            JSONObject()
                                .put("type", "Feature")
                                .put("geometry", JSONObject().put("type", "Point").put("coordinates", JSONArray().put(18.0).put(-34.0)))
                                .put(
                                    "properties",
                                    JSONObject()
                                        .put("feature_type", "paddock")
                                        .put("name", "North Camp")
                                        .put("paddock_id", "paddock-1")
                                        .put("grazing_pressure_ratio", 0.64)
                                        .put("water_alert_level", "warning")
                                        .put("water_alert_message", "Water needs checking"),
                                ),
                        ),
                ),
        )
}
