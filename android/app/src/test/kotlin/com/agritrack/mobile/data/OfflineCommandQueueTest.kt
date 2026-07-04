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
    fun mobCreateCommandShapesSupportedSyncPayload() {
        val command = MobileCommand.mobCreate(
            farmId = "farm-1",
            name = "New Heifers",
            originNote = "Bought at local sale",
        )

        val json = command.toJson()
        val payload = json.getJSONObject("payload")

        assertEquals("mob.create", json.getString("type"))
        assertEquals("farm-1", json.getString("farm_id"))
        assertEquals("New Heifers", payload.getString("name"))
        assertEquals("Bought at local sale", payload.getString("origin_note"))
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
    fun mobMoveCommandSupportsMultipleAllocations() {
        val command = MobileCommand.mobMoveAllocations(
            farmId = "farm-1",
            mobId = "mob-1",
            allocations = listOf("paddock-1" to 0.6, "paddock-2" to 0.4),
        )

        val allocations = command.toJson().getJSONObject("payload").getJSONArray("allocations")

        assertEquals(2, allocations.length())
        assertEquals("paddock-1", allocations.getJSONObject(0).getString("paddock_id"))
        assertEquals(0.4, allocations.getJSONObject(1).getDouble("allocation_fraction"), 0.0)
    }

    @Test
    fun mobMoveCommandSupportsCountAllocations() {
        val command = MobileCommand.mobMoveCountAllocations(
            farmId = "farm-1",
            mobId = "mob-1",
            allocations = listOf(
                MobMoveCountAllocation(
                    paddockId = "paddock-1",
                    groupCounts = listOf(MobMoveGroupCount("group-sheep", 12)),
                ),
                MobMoveCountAllocation(
                    paddockId = "paddock-2",
                    groupCounts = listOf(MobMoveGroupCount("group-cattle", 5)),
                ),
            ),
        )

        val payload = command.toJson().getJSONObject("payload")
        val allocations = payload.getJSONArray("allocations")

        assertEquals("mob.move", command.toJson().getString("type"))
        assertEquals("counts", payload.getString("allocation_mode"))
        assertEquals("paddock-1", allocations.getJSONObject(0).getString("paddock_id"))
        assertEquals(
            "group-sheep",
            allocations.getJSONObject(0).getJSONArray("group_counts").getJSONObject(0).getString("animal_group_type_id"),
        )
        assertEquals(
            5,
            allocations.getJSONObject(1).getJSONArray("group_counts").getJSONObject(0).getInt("head_count"),
        )
    }

    @Test
    fun paddockNoteCommandShapesSupportedSyncPayload() {
        val command = MobileCommand.paddockNote(
            farmId = "farm-1",
            paddockId = "paddock-1",
            description = "Pasture cover recovering",
            tags = listOf("pasture", "field"),
        )

        val json = command.toJson()

        assertEquals("paddock_event.create", json.getString("type"))
        assertEquals("farm-1", json.getString("farm_id"))
        assertEquals("paddock-1", json.getJSONObject("payload").getString("paddock_id"))
        assertEquals("pasture", json.getJSONObject("payload").getJSONArray("tags").getString(0))
    }

    @Test
    fun waterAssetNoteCommandShapesSupportedSyncPayload() {
        val command = MobileCommand.waterAssetNote(
            farmId = "farm-1",
            waterAssetId = "tank-1",
            description = "Float valve checked",
            tags = listOf("inspection", "water"),
        )

        val json = command.toJson()

        assertEquals("water_asset_event.create", json.getString("type"))
        assertEquals("farm-1", json.getString("farm_id"))
        assertEquals("tank-1", json.getJSONObject("payload").getString("water_asset_id"))
        assertEquals("Float valve checked", json.getJSONObject("payload").getString("description"))
        assertEquals("inspection", json.getJSONObject("payload").getJSONArray("tags").getString(0))
    }

    @Test
    fun fenceNoteCommandShapesSupportedSyncPayload() {
        val command = MobileCommand.fenceNote(
            farmId = "farm-1",
            fenceSectionId = "fence-1",
            description = "Found a hole under the mesh",
            tags = listOf("inspection", "jackal"),
            eventType = "inspection",
            conditionAfter = "bad",
        )

        val json = command.toJson()
        val payload = json.getJSONObject("payload")

        assertEquals("fence_event.create", json.getString("type"))
        assertEquals("farm-1", json.getString("farm_id"))
        assertEquals("fence-1", payload.getString("fence_section_id"))
        assertEquals("inspection", payload.getString("event_type"))
        assertEquals("bad", payload.getString("condition_after"))
        assertEquals("jackal", payload.getJSONArray("tags").getString(1))
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
    fun newGroupStockCountCommandShapesSupportedSyncPayload() {
        val command = MobileCommand.stockCount(
            farmId = "farm-1",
            mobId = "mob-1",
            species = "Sheep",
            breed = "Merino",
            sex = "ewe",
            ageClass = "adult",
            quantity = 18,
            note = "New ewe group",
        )

        val json = command.toJson()
        val payload = json.getJSONObject("payload")
        val group = payload.getJSONObject("animal_group_type")

        assertEquals("stock_count.record", json.getString("type"))
        assertEquals("mob-1", payload.getString("mob_id"))
        assertEquals("Sheep", group.getString("species"))
        assertEquals("Merino", group.getString("breed"))
        assertEquals("ewe", group.getString("sex"))
        assertEquals("adult", group.getString("age_class"))
        assertEquals(18, payload.getInt("quantity"))
        assertEquals("New ewe group", payload.getString("note"))
    }

    @Test
    fun shearingCommandsShapeSupportedSyncPayloads() {
        val shearer = MobileCommand.shearerCreate(
            farmId = "farm-1",
            shearerId = "shearer-1",
            name = "  Lootjie  ",
        ).toJson()
        val session = MobileCommand.shearingSessionCreate(
            farmId = "farm-1",
            sessionId = "session-1",
            name = "  October sheep  ",
            species = "Sheep",
            startDate = "2026-10-01",
            endDate = "2026-10-03",
            lootjieRate = 10.0,
            notes = "Main run",
        ).toJson()
        val existingGroupEntry = MobileCommand.shearingEntryRecord(
            farmId = "farm-1",
            entryId = "entry-1",
            sessionId = "session-1",
            workDate = "2026-10-01",
            shearerId = "shearer-1",
            animalGroupType = AnimalGroupTypeSummary("group-1", "Sheep", "Merino", "ram", "adult"),
            quantity = 4,
            note = "First day",
        ).toJson()
        val newGroupEntry = MobileCommand.shearingEntryRecord(
            farmId = "farm-1",
            entryId = "entry-2",
            sessionId = "session-1",
            workDate = "2026-10-02",
            shearerId = "shearer-1",
            animalGroupType = AnimalGroupTypeSummary("pending-group-1", "Sheep", "Dormer", "ewe", "adult"),
            quantity = 7,
            note = "",
        ).toJson()
        val close = MobileCommand.shearingSessionUpdate("farm-1", "session-1", "closed").toJson()
        val baleCode = MobileCommand.shearingBaleCodeUpsert(
            farmId = "farm-1",
            baleCodeId = "code-1",
            species = "Sheep",
            code = "  FH  ",
            lineType = "Fleece",
            ageGroup = "Adult",
            finenessGrade = "Fine",
            lengthCode = "b",
            finenessMicron = 21.5,
            cleanYieldPercent = 80.0,
            color = "White",
            vegetableMatter = "Low",
            styleCharacter = "Good character",
            consistency = "Even",
            fault = "None",
            description = "Fine hogget",
            notes = "",
        ).toJson()
        val bale = MobileCommand.shearingBaleRecord(
            farmId = "farm-1",
            baleId = "bale-1",
            sessionId = "session-1",
            baleCodeId = "code-1",
            codeText = "",
            baleNumber = "B1",
            weightKg = 80.0,
            pricePerKg = 20.0,
            totalPrice = null,
            notes = "Sold later",
        ).toJson()
        val deleteBale = MobileCommand.shearingBaleDelete("farm-1", "session-1", "bale-1").toJson()

        assertEquals("shearer.create", shearer.getString("type"))
        assertEquals("Lootjie", shearer.getJSONObject("payload").getString("name"))
        assertEquals(true, shearer.getJSONObject("payload").getBoolean("active"))
        assertEquals("shearing_session.create", session.getString("type"))
        assertEquals("October sheep", session.getJSONObject("payload").getString("name"))
        assertEquals("2026-10-03", session.getJSONObject("payload").getString("end_date"))
        assertEquals("shearing_entry.record", existingGroupEntry.getString("type"))
        assertEquals("group-1", existingGroupEntry.getJSONObject("payload").getString("animal_group_type_id"))
        assertEquals("adult", existingGroupEntry.getJSONObject("payload").getJSONObject("animal_group_type").getString("age_class"))
        assertEquals(false, newGroupEntry.getJSONObject("payload").has("animal_group_type_id"))
        assertEquals("Dormer", newGroupEntry.getJSONObject("payload").getJSONObject("animal_group_type").getString("breed"))
        assertEquals("shearing_session.update", close.getString("type"))
        assertEquals("closed", close.getJSONObject("payload").getString("status"))
        assertEquals("shearing_bale_code.upsert", baleCode.getString("type"))
        assertEquals("FH", baleCode.getJSONObject("payload").getString("code"))
        assertEquals("B", baleCode.getJSONObject("payload").getString("length_code"))
        assertEquals(21.5, baleCode.getJSONObject("payload").getDouble("fineness_micron"), 0.0)
        assertEquals(80.0, baleCode.getJSONObject("payload").getDouble("clean_yield_percent"), 0.0)
        assertEquals("shearing_bale.record", bale.getString("type"))
        assertEquals("code-1", bale.getJSONObject("payload").getString("bale_code_id"))
        assertEquals(80.0, bale.getJSONObject("payload").getDouble("weight_kg"), 0.0)
        assertEquals(false, bale.getJSONObject("payload").has("total_price"))
        assertEquals("shearing_bale.delete", deleteBale.getString("type"))
        assertEquals("bale-1", deleteBale.getJSONObject("payload").getString("bale_id"))
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
        val fence = MobileCommand.fenceUpdate(
            farmId = "farm-1",
            fenceSectionId = "fence-1",
            condition = "fair",
            notes = "Hotwire working again",
            electricWire = true,
        ).toJson()

        assertEquals("water_asset_status.update", water.getString("type"))
        assertEquals("full", water.getJSONObject("payload").getString("water_level"))
        assertEquals("paddock.update", paddock.getString("type"))
        assertEquals("resting", paddock.getJSONObject("payload").getString("status"))
        assertEquals("gate", paddock.getJSONObject("payload").getJSONArray("tags").getString(0))
        assertEquals("task.status.update", task.getString("type"))
        assertEquals("in_progress", task.getJSONObject("payload").getString("status"))
        assertEquals("fence_section.update", fence.getString("type"))
        assertEquals("fence-1", fence.getJSONObject("payload").getString("fence_section_id"))
        assertEquals(true, fence.getJSONObject("payload").getBoolean("electric_wire"))
    }

    @Test
    fun taskCreateCanTargetFenceSection() {
        val command = MobileCommand.taskCreate(
            farmId = "farm-1",
            heading = "Repair boundary fence",
            description = "Replace droppers",
            dueDate = "2026-06-15",
            entityType = "fence_section",
            entityId = "fence-1",
        )

        val payload = command.toJson().getJSONObject("payload")

        assertEquals("task.create", command.toJson().getString("type"))
        assertEquals("fence-1", payload.getString("fence_section_id"))
    }

    @Test
    fun gateUpdateCommandShapesSupportedSyncPayload() {
        val command = MobileCommand.gateUpdate(
            farmId = "farm-1",
            gateId = "gate-1",
            status = "open",
            closureChoices = listOf("mob-1" to "paddock-1"),
            eventTime = "2026-06-12T10:00:00Z",
        )

        val json = command.toJson()
        val payload = json.getJSONObject("payload")
        val choice = payload.getJSONArray("closure_choices").getJSONObject(0)

        assertEquals("gate.update", json.getString("type"))
        assertEquals("farm-1", json.getString("farm_id"))
        assertEquals("gate-1", payload.getString("gate_id"))
        assertEquals("open", payload.getString("status"))
        assertEquals("2026-06-12T10:00:00Z", payload.getString("event_time"))
        assertEquals("mob-1", choice.getString("mob_id"))
        assertEquals("paddock-1", choice.getString("component_paddock_id"))
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
