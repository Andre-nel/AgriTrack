package com.agritrack.mobile.data

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Test

class OfflineCommandQueueTest {
    @Test
    fun enqueueAndRemoveAppliedCommands() {
        val store = MemoryStore()
        val queue = OfflineCommandQueue(store)
        val first = MobileCommand.rainfall("farm-1", "2026-05-18", 2.5, "first")
        val second = MobileCommand.rainfall("farm-1", "2026-05-19", 3.0, "second")

        queue.enqueue(first)
        queue.enqueue(second)

        assertEquals(2, queue.size())

        val results = JSONArray()
            .put(
                JSONObject()
                    .put("client_command_id", first.clientCommandId)
                    .put("status", "applied")
            )
            .put(
                JSONObject()
                    .put("client_command_id", second.clientCommandId)
                    .put("status", "failed")
            )
        queue.removeApplied(results)

        assertEquals(1, queue.size())
        assertEquals(second.clientCommandId, queue.pending().first().clientCommandId)
    }

    @Test
    fun invalidFailedCommandsAreDroppedFromQueue() {
        val store = MemoryStore()
        val queue = OfflineCommandQueue(store)
        val invalid = MobileCommand.rainfall("farm-1", "not-a-date", 2.5, "")
        val retryable = MobileCommand.waterAssetStatus("farm-1", "missing-water", "", "full")

        queue.enqueue(invalid)
        queue.enqueue(retryable)

        val results = JSONArray()
            .put(
                JSONObject()
                    .put("client_command_id", invalid.clientCommandId)
                    .put("status", "failed")
                    .put(
                        "error",
                        JSONObject()
                            .put("code", "invalid_command")
                            .put("message", "recorded_on must be a valid ISO date"),
                    )
            )
            .put(
                JSONObject()
                    .put("client_command_id", retryable.clientCommandId)
                    .put("status", "failed")
                    .put(
                        "error",
                        JSONObject()
                            .put("code", "not_found")
                            .put("message", "Water asset not found for this farm"),
                    )
            )

        queue.removeApplied(results)

        assertEquals(1, queue.size())
        assertEquals(retryable.clientCommandId, queue.pending().first().clientCommandId)
    }

    @Test
    fun mobNoteCommandShapesSupportedSyncPayload() {
        val command = MobileCommand.mobNote(
            farmId = "farm-1",
            mobId = "mob-1",
            description = "Settled after move",
            tags = listOf("condition", "field"),
        )

        val json = command.toJson()

        assertEquals("mob_event.create", json.getString("type"))
        assertEquals("farm-1", json.getString("farm_id"))
        assertEquals("mob-1", json.getJSONObject("payload").getString("mob_id"))
        assertEquals("condition", json.getJSONObject("payload").getJSONArray("tags").getString(0))
    }

    @Test
    fun mobMoveCommandShapesSupportedSyncPayload() {
        val command = MobileCommand.mobMove(
            farmId = "farm-1",
            mobId = "mob-1",
            paddockId = "paddock-1",
        )

        val json = command.toJson()
        val allocation = json.getJSONObject("payload").getJSONArray("allocations").getJSONObject(0)

        assertEquals("mob.move", json.getString("type"))
        assertEquals("mob-1", json.getJSONObject("payload").getString("mob_id"))
        assertEquals("paddock-1", allocation.getString("paddock_id"))
        assertEquals(1.0, allocation.getDouble("allocation_fraction"), 0.0)
    }

    @Test
    fun stockCountCommandShapesSupportedSyncPayload() {
        val command = MobileCommand.stockCount(
            farmId = "farm-1",
            mobId = "mob-1",
            animalGroupTypeId = "group-1",
            quantity = 42,
            note = "Counted at crush",
        )

        val json = command.toJson()
        val payload = json.getJSONObject("payload")

        assertEquals("stock_count.record", json.getString("type"))
        assertEquals("mob-1", payload.getString("mob_id"))
        assertEquals("group-1", payload.getString("animal_group_type_id"))
        assertEquals(42, payload.getInt("quantity"))
        assertEquals("Counted at crush", payload.getString("note"))
    }

    @Test
    fun fieldEditCommandsShapeSupportedSyncPayloads() {
        val water = MobileCommand.waterAssetStatus(
            farmId = "farm-1",
            waterAssetId = "tank-1",
            status = "operational",
            waterLevel = "full",
            active = true,
        ).toJson()
        val paddock = MobileCommand.paddockUpdate(
            farmId = "farm-1",
            paddockId = "paddock-1",
            status = "resting",
            notes = "Gate latch needs work",
            tags = "gate, field",
        ).toJson()
        val task = MobileCommand.taskStatus(
            farmId = "farm-1",
            taskId = "task-1",
            status = "in_progress",
            note = "Started",
        ).toJson()

        assertEquals("water_asset_status.update", water.getString("type"))
        assertEquals("full", water.getJSONObject("payload").getString("water_level"))
        assertEquals("paddock.update", paddock.getString("type"))
        assertEquals("resting", paddock.getJSONObject("payload").getString("status"))
        assertEquals("gate", paddock.getJSONObject("payload").getJSONArray("tags").getString(0))
        assertEquals("task.status.update", task.getString("type"))
        assertEquals("in_progress", task.getJSONObject("payload").getString("status"))
    }

    @Test
    fun mobTransferCommandShapesSupportedSyncPayload() {
        val command = MobileCommand.mobTransfer(
            farmId = "farm-1",
            sourceMobId = "mob-1",
            destinationMobId = "mob-2",
            animalGroupTypeId = "group-1",
            quantity = 8,
            note = "Even up mobs",
        )

        val payload = command.toJson().getJSONObject("payload")
        val transfer = payload.getJSONArray("transfers").getJSONObject(0)

        assertEquals("mob.transfer", command.toJson().getString("type"))
        assertEquals("mob-1", payload.getString("source_mob_id"))
        assertEquals("mob-2", payload.getString("destination_mob_id"))
        assertEquals("group-1", transfer.getString("animal_group_type_id"))
        assertEquals(8, transfer.getInt("quantity"))
    }

    private class MemoryStore : OfflineCommandQueue.CommandStore {
        private var value = "[]"

        override fun read(): String = value

        override fun write(value: String) {
            this.value = value
        }
    }
}
