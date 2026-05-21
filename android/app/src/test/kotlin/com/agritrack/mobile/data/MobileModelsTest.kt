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
        )

        assertEquals("North Block", snapshot.farm.name)
        assertEquals(1, snapshot.paddockCount)
        assertEquals(1, snapshot.mobCount)
        assertEquals(1, snapshot.waterAssetCount)
        assertEquals("North Camp", snapshot.paddocks.first().name)
        assertEquals("Main Mob", snapshot.mobs.first().name)
        assertEquals(37, snapshot.mobs.first().balances.first().headCount)
        assertEquals("Cattle Bonsmara cow adult", snapshot.mobs.first().balances.first().animalGroupType.label)
    }

    @Test
    fun syncSummaryCountsAppliedAndFailedResults() {
        val summary = SyncSummary(
            results = listOf(
                SyncResult("rain-1", "rainfall.create", "applied", false, null),
                SyncResult("note-1", "mob_event.create", "failed", false, "Mob not found"),
            ),
            remainingQueueCount = 1,
        )

        assertEquals(1, summary.appliedCount)
        assertEquals(1, summary.failedCount)
        assertEquals(1, summary.remainingQueueCount)
    }
}
