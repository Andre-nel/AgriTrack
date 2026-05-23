package com.agritrack.mobile.data

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Test

class MobileModelsTest {
    @Test
    fun snapshotParserKeepsFarmCountsAndMobs() {
        val snapshot = FarmSnapshot.fromJson(
            JSONObject()
                .put(
                    "farm",
                    JSONObject()
                        .put("id", "farm-1")
                        .put("name", "North Block")
                        .put("timezone", "Africa/Johannesburg")
                )
                .put(
                    "paddocks",
                    JSONArray()
                        .put(
                            JSONObject()
                                .put("id", "paddock-1")
                                .put("name", "North Camp")
                        )
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
                                    JSONArray()
                                        .put(
                                            JSONObject()
                                                .put("id", "balance-1")
                                                .put("animal_group_type_id", "group-1")
                                                .put("head_count", 37)
                                                .put(
                                                    "animal_group_type",
                                                    JSONObject()
                                                        .put("id", "group-1")
                                                        .put("species", "Cattle")
                                                        .put("breed", "Bonsmara")
                                                        .put("sex", "cow")
                                                        .put("age_class", "adult")
                                                )
                                        )
                                )
                        )
                )
                .put("water_assets", JSONArray().put(JSONObject().put("id", "tank-1")))
                .put("rainfall", JSONArray().put(JSONObject().put("id", "rain-1")))
                .put("mob_events", JSONArray().put(JSONObject().put("id", "event-1")))
                .put(
                    "tasks",
                    JSONArray()
                        .put(
                            JSONObject()
                                .put("id", "task-1")
                                .put("display_key", "OPS-1")
                                .put("heading", "Check water")
                                .put("status", "todo")
                                .put("status_label", "TO DO")
                                .put("attachment_count", 1)
                                .put(
                                    "attachments",
                                    JSONArray()
                                        .put(
                                            JSONObject()
                                                .put("id", "attachment-1")
                                                .put("client_attachment_id", "photo-1")
                                                .put("original_filename", "trough.jpg")
                                                .put("content_type", "image/jpeg")
                                                .put("byte_size", 12)
                                        )
                                )
                                .put(
                                    "entity_links",
                                    JSONArray()
                                        .put(
                                            JSONObject()
                                                .put("id", "link-1")
                                                .put("task_id", "task-1")
                                                .put("entity_type", "paddock")
                                                .put("entity_id", "paddock-1")
                                        )
                                )
                        )
                )
                .put(
                    "calendar_items",
                    JSONArray()
                        .put(
                            JSONObject()
                                .put("kind", "task")
                                .put("date", "2026-05-22")
                                .put("title", "Check water")
                        )
                )
                .put(
                    "decision_feed",
                    JSONArray()
                        .put(
                            JSONObject()
                                .put("severity", "high")
                                .put("category", "water")
                                .put("title", "Water risk")
                                .put("detail", "North trough is empty")
                        )
                )
                .put(
                    "map_features",
                    JSONArray()
                        .put(
                            JSONObject()
                                .put("type", "Feature")
                                .put(
                                    "geometry",
                                    JSONObject()
                                        .put("type", "Point")
                                        .put("coordinates", JSONArray().put(18.0).put(-34.0))
                                )
                                .put(
                                    "properties",
                                    JSONObject()
                                        .put("feature_type", "paddock")
                                        .put("name", "North Camp")
                                        .put("paddock_id", "paddock-1")
                                        .put("grazing_pressure_ratio", 0.42)
                                )
                        )
                )
        )

        assertEquals("North Block", snapshot.farm.name)
        assertEquals(1, snapshot.paddockCount)
        assertEquals(1, snapshot.mobCount)
        assertEquals(1, snapshot.waterAssetCount)
        assertEquals("North Camp", snapshot.paddocks.first().name)
        assertEquals("Main Mob", snapshot.mobs.first().name)
        assertEquals(37, snapshot.mobs.first().balances.first().headCount)
        assertEquals("Cattle Bonsmara cow adult", snapshot.mobs.first().balances.first().animalGroupType.label)
        assertEquals("OPS-1", snapshot.tasks.first().displayKey)
        assertEquals(true, snapshot.tasks.first().isLinkedTo("paddock", "paddock-1"))
        assertEquals(1, snapshot.tasks.first().attachmentCount)
        assertEquals("trough.jpg", snapshot.tasks.first().attachments.first().originalFilename)
        assertEquals(0.42, snapshot.mapFeatures.first().grazingPressureRatio ?: 0.0, 0.0)
        assertEquals(1, snapshot.calendarItemCount)
        assertEquals(1, snapshot.decisionCount)
    }

    @Test
    fun bootstrapParserKeepsFormOptions() {
        val bootstrap = BootstrapResult.fromJson(
            JSONObject()
                .put("farms", JSONArray())
                .put("animal_group_types", JSONArray())
                .put(
                    "sync",
                    JSONObject().put("supported_command_types", JSONArray().put("task.create"))
                )
                .put(
                    "form_options",
                    JSONObject()
                        .put(
                            "task_statuses",
                            JSONArray().put(JSONObject().put("value", "todo").put("label", "TO DO"))
                        )
                        .put(
                            "water_status_options_by_type",
                            JSONObject().put(
                                "tank",
                                JSONArray().put(JSONObject().put("value", "operational").put("label", "Operational"))
                            )
                        )
                        .put("water_level_asset_types", JSONArray().put("tank"))
                        .put(
                            "water_level_options",
                            JSONArray().put(JSONObject().put("value", "full").put("label", "Full"))
                        )
                )
        )

        assertEquals("task.create", bootstrap.supportedCommandTypes.first())
        assertEquals("todo", bootstrap.formOptions.taskStatuses.first().value)
        assertEquals("operational", bootstrap.formOptions.waterStatusOptionsByType["tank"]?.first()?.value)
        assertEquals(true, "tank" in bootstrap.formOptions.waterLevelAssetTypes)
    }

    @Test
    fun syncSummaryCountsAppliedAndFailedResults() {
        val summary = SyncSummary(
            results = listOf(
                SyncResult("rain-1", "rainfall.create", "applied", false, null, null),
                SyncResult("note-1", "mob_event.create", "failed", false, "Mob not found", null),
            ),
            remainingQueueCount = 1,
        )

        assertEquals(1, summary.appliedCount)
        assertEquals(1, summary.failedCount)
        assertEquals(1, summary.remainingQueueCount)
    }

    @Test
    fun syncResultKeepsResponsePayload() {
        val result = SyncResult.fromJson(
            JSONObject()
                .put("client_command_id", "task-create-1")
                .put("type", "task.create")
                .put("status", "applied")
                .put("duplicate", false)
                .put("response", JSONObject().put("task", JSONObject().put("id", "task-1")))
        )

        assertEquals("task-1", result.response?.getJSONObject("task")?.getString("id"))
    }
}
