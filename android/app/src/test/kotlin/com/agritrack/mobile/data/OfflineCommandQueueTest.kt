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

    private class MemoryStore : OfflineCommandQueue.CommandStore {
        private var value = "[]"

        override fun read(): String = value

        override fun write(value: String) {
            this.value = value
        }
    }
}
