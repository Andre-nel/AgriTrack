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

    private class MemoryStore : OfflineCommandQueue.CommandStore {
        private var value = "[]"

        override fun read(): String = value

        override fun write(value: String) {
            this.value = value
        }
    }
}
