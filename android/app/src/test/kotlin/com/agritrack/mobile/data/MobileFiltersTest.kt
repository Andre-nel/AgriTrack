package com.agritrack.mobile.data

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Test

class MobileFiltersTest {
    @Test
    fun paddockFiltersUseNameStockTagAndPressure() {
        val snapshot = filterSnapshot()

        val filtered = filterPaddocks(
            snapshot,
            PaddockFilterState(name = "north", stockMin = "10", tag = "water", pressureMin = "40", pressureMax = "50"),
        )

        assertEquals(listOf("North Camp"), filtered.map { it.name })
    }

    @Test
    fun mobFiltersUseNameCountAndSpecies() {
        val snapshot = filterSnapshot()

        val filtered = filterMobs(
            snapshot.mobs,
            MobFilterState(name = "main", countMin = "10", countMax = "20", species = setOf("Cattle")),
        )

        assertEquals(listOf("Main Mob"), filtered.map { it.name })
    }

    @Test
    fun waterFiltersUseNameTypeStatusAndLevel() {
        val snapshot = filterSnapshot()

        val filtered = filterWaterAssets(
            snapshot.waterAssets,
            WaterFilterState(name = "tank", assetType = "tank", status = "operational", waterLevel = "full"),
        )

        assertEquals(listOf("Header Tank"), filtered.map { it.name })
    }

    @Test
    fun waterFiltersMatchUnknownStatusAndLevel() {
        val snapshot = filterSnapshot()

        val filtered = filterWaterAssets(
            snapshot.waterAssets,
            WaterFilterState(status = "unknown", waterLevel = "unknown"),
        )

        assertEquals(listOf("Hill Bore"), filtered.map { it.name })
    }

    @Test
    fun calendarTaskOnlyFiltersExcludeActivities() {
        val snapshot = filterSnapshot()

        val filtered = filterCalendarItems(
            snapshot.calendarItems,
            CalendarFilterState(
                startDate = "2026-05-01",
                endDate = "2026-05-31",
                itemType = "",
                name = "water",
                stage = "todo",
                assignee = "field",
                tag = "water",
                paddockId = "paddock-1",
                waterAssetId = "tank-1",
                mobId = "mob-1",
            ),
        )

        assertEquals(listOf("task"), filtered.map { it.kind })
        assertEquals(listOf("Check water"), filtered.map { it.title })
    }

    @Test
    fun calendarTagFilterIncludesIncidents() {
        val snapshot = filterSnapshot()

        val filtered = filterCalendarItems(
            snapshot.calendarItems,
            CalendarFilterState(tag = "stock"),
        )

        assertEquals(listOf("incident"), filtered.map { it.kind })
        assertEquals(listOf("Stock missing"), filtered.map { it.title })
    }

    private fun filterSnapshot(): FarmSnapshot =
        FarmSnapshot.fromJson(
            JSONObject()
                .put(
                    "farm",
                    JSONObject()
                        .put("id", "farm-1")
                        .put("name", "Filter Farm")
                        .put("timezone", "SAST"),
                )
                .put(
                    "paddocks",
                    JSONArray()
                        .put(JSONObject().put("id", "paddock-1").put("name", "North Camp").put("tags", JSONArray().put("water")))
                        .put(JSONObject().put("id", "paddock-2").put("name", "South Camp").put("tags", JSONArray().put("rest"))),
                )
                .put(
                    "active_grazing_by_paddock",
                    JSONArray()
                        .put(JSONObject().put("paddock_id", "paddock-1").put("total_head", 12.0))
                        .put(JSONObject().put("paddock_id", "paddock-2").put("total_head", 4.0)),
                )
                .put(
                    "mobs",
                    JSONArray()
                        .put(
                            JSONObject()
                                .put("id", "mob-1")
                                .put("name", "Main Mob")
                                .put("status", "active")
                                .put(
                                    "balances",
                                    JSONArray().put(balance("balance-1", "group-1", "Cattle", 12)),
                                ),
                        )
                        .put(
                            JSONObject()
                                .put("id", "mob-2")
                                .put("name", "Dry Ewes")
                                .put("status", "active")
                                .put(
                                    "balances",
                                    JSONArray().put(balance("balance-2", "group-2", "Sheep", 40)),
                                ),
                        ),
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
                                .put("status", "operational")
                                .put("water_level", "full"),
                        )
                        .put(
                            JSONObject()
                                .put("id", "trough-1")
                                .put("name", "South Trough")
                                .put("asset_type", "trough")
                                .put("asset_type_label", "Trough")
                                .put("status", "blocked")
                                .put("water_level", "low"),
                        )
                        .put(
                            JSONObject()
                                .put("id", "bore-1")
                                .put("name", "Hill Bore")
                                .put("asset_type", "bore")
                                .put("asset_type_label", "Bore"),
                        ),
                )
                .put(
                    "calendar_items",
                    JSONArray()
                        .put(
                            JSONObject()
                                .put("kind", "task")
                                .put("date", "2026-05-22")
                                .put("source_id", "task-1")
                                .put("task_id", "task-1")
                                .put("title", "Check water")
                                .put("stage", "todo")
                                .put("assignee_name", "Field Team")
                                .put("tags", JSONArray().put("water"))
                                .put(
                                    "entity_links",
                                    JSONArray()
                                        .put(entityLink("link-1", "task-1", "paddock", "paddock-1"))
                                        .put(entityLink("link-2", "task-1", "water_asset", "tank-1"))
                                        .put(entityLink("link-3", "task-1", "mob", "mob-1")),
                                ),
                        )
                        .put(
                            JSONObject()
                                .put("kind", "activity")
                                .put("date", "2026-05-22")
                                .put("source_id", "activity-1")
                                .put("activity_id", "activity-1")
                                .put("title", "Weekly water run")
                                .put("stage", "activity"),
                        )
                        .put(
                            JSONObject()
                                .put("kind", "incident")
                                .put("date", "2026-05-23")
                                .put("source_id", "incident-1")
                                .put("incident_id", "incident-1")
                                .put("title", "Stock missing")
                                .put("stage", "incident")
                                .put("tags", JSONArray().put("stock").put("security")),
                        ),
                )
                .put(
                    "map_features",
                    JSONArray()
                        .put(mapFeature("paddock-1", "North Camp", 0.42))
                        .put(mapFeature("paddock-2", "South Camp", 0.15)),
                ),
        )

    private fun balance(id: String, groupId: String, species: String, headCount: Int): JSONObject =
        JSONObject()
            .put("id", id)
            .put("animal_group_type_id", groupId)
            .put("head_count", headCount)
            .put(
                "animal_group_type",
                JSONObject()
                    .put("id", groupId)
                    .put("species", species)
                    .put("breed", "")
                    .put("sex", "")
                    .put("age_class", ""),
            )

    private fun entityLink(id: String, taskId: String, type: String, entityId: String): JSONObject =
        JSONObject()
            .put("id", id)
            .put("task_id", taskId)
            .put("entity_type", type)
            .put("entity_id", entityId)

    private fun mapFeature(paddockId: String, name: String, pressure: Double): JSONObject =
        JSONObject()
            .put("type", "Feature")
            .put("geometry", JSONObject().put("type", "Point").put("coordinates", JSONArray().put(18.0).put(-34.0)))
            .put(
                "properties",
                JSONObject()
                    .put("feature_type", "paddock")
                    .put("name", name)
                    .put("paddock_id", paddockId)
                    .put("grazing_pressure_ratio", pressure),
            )
}
