package com.agritrack.mobile.data

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Test

class OptimisticSnapshotTest {
    @Test
    fun pendingFieldCommandsAreVisibleInSnapshot() {
        val base = fieldSnapshot()
        val commands = JSONArray()
            .put(MobileCommand.stockCount("farm-1", "mob-1", "group-1", 14, "Counted").toJson())
            .put(MobileCommand.mobMove("farm-1", "mob-1", "paddock-2").toJson())
            .put(
                MobileCommand.paddockUpdate(
                    farmId = "farm-1",
                    paddockId = "paddock-1",
                    status = "resting",
                    notes = "Gate latch needs work",
                    tags = "gate, field",
                ).toJson()
            )
            .put(MobileCommand.waterAssetStatus("farm-1", "tank-1", "limited", "low", active = false).toJson())
            .put(MobileCommand.mobNote("farm-1", "mob-1", "Settled after move", listOf("move")).toJson())
            .put(MobileCommand.paddockNote("farm-1", "paddock-1", "Pasture recovering", listOf("pasture")).toJson())
            .put(MobileCommand.waterAssetNote("farm-1", "tank-1", "Tank checked", listOf("water")).toJson())
            .put(MobileCommand.fenceUpdate("farm-1", "fence-1", "bad", "Loose bottom wire", electricWire = true).toJson())
            .put(MobileCommand.fenceNote("farm-1", "fence-1", "Packed stones under the fence", listOf("fence"), conditionAfter = "fair").toJson())
            .put(MobileCommand.rainfall("farm-1", "2026-05-31", 8.5, "Storm").toJson())

        val optimistic = base.withOptimisticCommands(commands)

        val paddock = optimistic.paddocks.first { it.id == "paddock-1" }
        assertEquals("resting", paddock.status)
        assertEquals("Gate latch needs work", paddock.notes)
        assertEquals(listOf("gate", "field"), paddock.tags)
        assertEquals("limited", optimistic.waterAssets.first().status)
        assertEquals("low", optimistic.waterAssets.first().waterLevel)
        assertEquals(false, optimistic.waterAssets.first().active)
        assertEquals(14, optimistic.mobs.first { it.id == "mob-1" }.totalHead)
        assertEquals(listOf("paddock-2"), mobGrazingPaddocks(optimistic, "mob-1").map { it.paddockId })
        assertEquals(14.0, paddockGrazing(optimistic, "paddock-2")?.totalHead ?: 0.0, 0.0)
        assertEquals("Storm", optimistic.rainfall.first().note)
        assertEquals("Settled after move", optimistic.mobEvents.first().description)
        assertEquals("Pasture recovering", optimistic.paddockEvents.first().description)
        assertEquals("Tank checked", optimistic.waterAssetEvents.first().description)
        assertEquals("fair", optimistic.fenceSections.first().condition)
        assertEquals(true, optimistic.fenceSections.first().electricWire)
        assertEquals("Packed stones under the fence", optimistic.fenceEvents.first().description)
        assertEquals("fair", optimistic.mapFeatures.first { it.fenceSectionId == "fence-1" }.fenceCondition)
    }

    @Test
    fun pendingTaskCommandsCreateUpdateAndAnnotateTasks() {
        val base = fieldSnapshot()
        val create = MobileCommand.taskCreate(
            farmId = "farm-1",
            heading = "Fix gate",
            description = "North gate latch",
            dueDate = "2026-06-02",
            entityType = "paddock",
            entityId = "paddock-1",
        )
        val commands = JSONArray()
            .put(create.toJson())
            .put(MobileCommand.taskStatus("farm-1", "task-1", "closed", "Finished in the field").toJson())
            .put(MobileCommand.taskComment("farm-1", "task-1", "Left spare bolts in the bakkie").toJson())
        val photos = JSONArray()
            .put(
                JSONObject()
                    .put("client_attachment_id", "photo-1")
                    .put("farm_id", "farm-1")
                    .put("target_client_command_id", create.clientCommandId)
                    .put("original_filename", "gate.jpg")
                    .put("content_type", "image/jpeg")
                    .put("byte_size", 512),
            )

        val optimistic = base.withOptimisticCommands(commands).withOptimisticTaskPhotos(photos)

        val newTask = optimistic.tasks.first { it.heading == "Fix gate" }
        assertEquals("Pending", newTask.displayKey)
        assertEquals(true, newTask.isLinkedTo("paddock", "paddock-1"))
        assertEquals(1, newTask.attachmentCount)
        assertEquals("gate.jpg", newTask.attachments.first().originalFilename)
        assertNotNull(optimistic.calendarItems.firstOrNull { it.taskId == newTask.id && it.date == "2026-06-02" })

        val updatedTask = optimistic.tasks.first { it.id == "task-1" }
        assertEquals("closed", updatedTask.status)
        assertEquals("Closed", updatedTask.statusLabel)
        assertEquals(2, updatedTask.commentCount)
        assertEquals("Left spare bolts in the bakkie", updatedTask.comments.last().body)
        assertEquals(
            "Closed",
            optimistic.calendarItems.first { it.taskId == "task-1" }.stageLabel,
        )
    }

    @Test
    fun pendingGateUpdateRedistributesGrazingAndUpdatesMapStatus() {
        val base = fieldSnapshot()
        val commands = JSONArray()
            .put(
                MobileCommand.gateUpdate(
                    farmId = "farm-1",
                    gateId = "gate-1",
                    status = "open",
                    eventTime = "2026-06-12T10:00:00Z",
                ).toJson()
            )

        val optimistic = base.withOptimisticCommands(commands)

        assertEquals("open", optimistic.gates.first { it.id == "gate-1" }.status)
        assertEquals("open", optimistic.mapFeatures.first { it.gateId == "gate-1" }.gateStatus)
        val allocations = optimistic.activeGrazing.first { it.mobId == "mob-1" }.allocations
            .associate { it.paddockId to it.allocationFraction }
        assertEquals(0.25, allocations["paddock-1"] ?: 0.0, 0.0)
        assertEquals(0.75, allocations["paddock-2"] ?: 0.0, 0.0)
        assertEquals(2.5, paddockGrazing(optimistic, "paddock-1")?.totalHead ?: 0.0, 0.0)
        assertEquals(7.5, paddockGrazing(optimistic, "paddock-2")?.totalHead ?: 0.0, 0.0)
    }

    @Test
    fun pendingMoveIntoAlreadyOpenGateUsesGateAllocations() {
        val raw = JSONObject(fieldSnapshot().rawJson)
        raw.getJSONArray("gates").getJSONObject(0).put("status", "open")
        raw.getJSONArray("map_features")
            .getJSONObject(0)
            .getJSONObject("properties")
            .put("status", "open")
        val base = FarmSnapshot.fromJson(raw)
        val commands = JSONArray()
            .put(MobileCommand.mobMove("farm-1", "mob-1", "paddock-2").toJson())

        val optimistic = base.withOptimisticCommands(commands)

        val allocations = optimistic.activeGrazing.first { it.mobId == "mob-1" }.allocations
            .associate { it.paddockId to it.allocationFraction }
        assertEquals(0.25, allocations["paddock-1"] ?: 0.0, 0.0)
        assertEquals(0.75, allocations["paddock-2"] ?: 0.0, 0.0)
        assertEquals(2.5, paddockGrazing(optimistic, "paddock-1")?.totalHead ?: 0.0, 0.0)
        assertEquals(7.5, paddockGrazing(optimistic, "paddock-2")?.totalHead ?: 0.0, 0.0)
    }

    @Test
    fun pendingCountMoveUsesExactGroupCountsForPaddockGrazing() {
        val base = fieldSnapshot()
        val commands = JSONArray()
            .put(
                MobileCommand.mobMoveCountAllocations(
                    farmId = "farm-1",
                    mobId = "mob-1",
                    allocations = listOf(
                        MobMoveCountAllocation(
                            paddockId = "paddock-1",
                            groupCounts = listOf(MobMoveGroupCount("group-1", 4)),
                        ),
                        MobMoveCountAllocation(
                            paddockId = "paddock-2",
                            groupCounts = listOf(MobMoveGroupCount("group-1", 6)),
                        ),
                    ),
                ).toJson()
            )

        val optimistic = base.withOptimisticCommands(commands)
        val allocations = optimistic.activeGrazing.first { it.mobId == "mob-1" }.allocations
            .associate { it.paddockId to it.allocationFraction }

        assertEquals(0.4, allocations["paddock-1"] ?: 0.0, 0.0)
        assertEquals(0.6, allocations["paddock-2"] ?: 0.0, 0.0)
        assertEquals(4.0, paddockGrazing(optimistic, "paddock-1")?.totalHead ?: 0.0, 0.0)
        assertEquals(6.0, paddockGrazing(optimistic, "paddock-2")?.totalHead ?: 0.0, 0.0)
    }

    @Test
    fun pendingShearingCommandsCreateTotalsAndRemoveZeroQuantityRows() {
        val base = shearingSnapshot()
        val createShearer = MobileCommand.shearerCreate("farm-1", "shearer-1", "Lootjie")
        val createSession = MobileCommand.shearingSessionCreate(
            farmId = "farm-1",
            sessionId = "session-1",
            name = "October sheep",
            species = "Sheep",
            startDate = "2026-10-01",
            endDate = "2026-10-03",
            lootjieRate = 10.0,
            notes = "",
        )
        val recordAdultRam = MobileCommand.shearingEntryRecord(
            farmId = "farm-1",
            entryId = "entry-1",
            sessionId = "session-1",
            workDate = "2026-10-01",
            shearerId = "shearer-1",
            animalGroupType = AnimalGroupTypeSummary("group-1", "Sheep", "Merino", "ram", "adult"),
            quantity = 4,
            note = "First day",
        )
        val baleCode = MobileCommand.shearingBaleCodeUpsert(
            farmId = "farm-1",
            baleCodeId = "code-1",
            species = "Sheep",
            code = "FH",
            lineType = "Fleece",
            ageGroup = "Adult",
            finenessGrade = "Fine",
            lengthCode = "B",
            finenessMicron = 21.5,
            cleanYieldPercent = 80.0,
            color = "",
            vegetableMatter = "",
            styleCharacter = "Good character",
            consistency = "Even",
            fault = "",
            description = "",
            notes = "",
        )
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
            notes = "First bale",
        )

        val withEntry = base.withOptimisticCommands(
            JSONArray()
                .put(createShearer.toJson())
                .put(createSession.toJson())
                .put(baleCode.toJson())
                .put(bale.toJson())
                .put(recordAdultRam.toJson())
        )

        val session = withEntry.shearingSessions.first()
        assertEquals(1, withEntry.shearerCount)
        assertEquals("Lootjie", withEntry.shearers.first().name)
        assertEquals(1, withEntry.shearingBaleCodeCount)
        assertEquals("Fine", withEntry.shearingBaleCodes.first().finenessGrade)
        assertEquals("B", withEntry.shearingBaleCodes.first().lengthCode)
        assertEquals(80.0, withEntry.shearingBaleCodes.first().cleanYieldPercent ?: 0.0, 0.0)
        assertEquals(4, session.totalQuantity)
        assertEquals(80.0, session.totalAmount, 0.0)
        assertEquals(1, session.baleMoneyTotals.totalBales)
        assertEquals(80.0, session.baleMoneyTotals.totalKg, 0.0)
        assertEquals(1600.0, session.baleMoneyTotals.totalPrice, 0.0)
        assertEquals(20.0, session.baleMoneyTotals.averagePricePerKg ?: 0.0, 0.0)
        assertEquals("FH", session.baleSummaryByCode.first().code)
        assertEquals(2.0, session.entries.first().multiplier, 0.0)
        assertEquals(20.0, session.entries.first().unitRate, 0.0)
        assertEquals("Lootjie", session.byShearer.first().shearerName)

        val removeAdultRam = MobileCommand.shearingEntryRecord(
            farmId = "farm-1",
            entryId = "entry-1",
            sessionId = "session-1",
            workDate = "2026-10-01",
            shearerId = "shearer-1",
            animalGroupType = AnimalGroupTypeSummary("group-1", "Sheep", "Merino", "ram", "adult"),
            quantity = 0,
            note = "",
        )
        val removed = base.withOptimisticCommands(
            JSONArray()
                .put(createShearer.toJson())
                .put(createSession.toJson())
                .put(baleCode.toJson())
                .put(bale.toJson())
                .put(recordAdultRam.toJson())
                .put(removeAdultRam.toJson())
                .put(MobileCommand.shearingBaleDelete("farm-1", "session-1", "bale-1").toJson())
        )

        assertEquals(0, removed.shearingSessions.first().totalQuantity)
        assertEquals(0, removed.shearingSessions.first().entries.size)
        assertEquals(0, removed.shearingSessions.first().baleMoneyTotals.totalBales)
        assertEquals(0, removed.shearingSessions.first().bales.size)
    }

    private fun fieldSnapshot(): FarmSnapshot =
        FarmSnapshot.fromJson(
            JSONObject()
                .put("farm", JSONObject().put("id", "farm-1").put("name", "North Farm").put("timezone", "UTC"))
                .put(
                    "paddocks",
                    JSONArray()
                        .put(
                            JSONObject()
                                .put("id", "paddock-1")
                                .put("name", "North Camp")
                                .put("status", "active")
                                .put("area_ha", 10.0)
                                .put("grazeable_area_ha", 10.0),
                        )
                        .put(
                            JSONObject()
                                .put("id", "paddock-2")
                                .put("name", "South Camp")
                                .put("status", "active")
                                .put("area_ha", 30.0)
                                .put("grazeable_area_ha", 30.0),
                        ),
                )
                .put(
                    "mobs",
                    JSONArray()
                        .put(
                            JSONObject()
                                .put("id", "mob-1")
                                .put("name", "Main Mob")
                                .put("status", "active")
                                .put("balances", JSONArray().put(balance("balance-1", "mob-1", "group-1", 10))),
                        )
                        .put(
                            JSONObject()
                                .put("id", "mob-2")
                                .put("name", "Second Mob")
                                .put("status", "active")
                                .put("balances", JSONArray().put(balance("balance-2", "mob-2", "group-1", 4))),
                        ),
                )
                .put(
                    "active_grazing",
                    JSONArray().put(
                        JSONObject()
                            .put("id", "grazing-1")
                            .put("mob_id", "mob-1")
                            .put("start_at", "2026-05-24T12:00:00+00:00")
                            .put(
                                "allocations",
                                JSONArray().put(JSONObject().put("paddock_id", "paddock-1").put("allocation_fraction", 1.0)),
                            ),
                    ),
                )
                .put(
                    "active_grazing_by_paddock",
                    JSONArray().put(
                        JSONObject()
                            .put("paddock_id", "paddock-1")
                            .put("total_head", 10.0)
                            .put(
                                "mobs",
                                JSONArray().put(JSONObject().put("mob_id", "mob-1").put("mob_name", "Main Mob").put("allocation_pct", 100.0)),
                            ),
                    ),
                )
                .put(
                    "gates",
                    JSONArray().put(
                        JSONObject()
                            .put("id", "gate-1")
                            .put("gate_id", "gate-1")
                            .put("farm_id", "farm-1")
                            .put("paddock_a_id", "paddock-1")
                            .put("paddock_a_name", "North Camp")
                            .put("paddock_b_id", "paddock-2")
                            .put("paddock_b_name", "South Camp")
                            .put("name", "North Camp / South Camp Gate")
                            .put("status", "closed")
                            .put("active", true)
                            .put("source", "manual"),
                    ),
                )
                .put(
                    "water_assets",
                    JSONArray().put(
                        JSONObject()
                            .put("id", "tank-1")
                            .put("name", "Header Tank")
                            .put("asset_type", "tank")
                            .put("asset_type_label", "Tank")
                            .put("status", "operational")
                            .put("water_level", "full")
                            .put("active", true),
                    ),
                )
                .put(
                    "fence_sections",
                    JSONArray().put(
                        JSONObject()
                            .put("id", "fence-1")
                            .put("farm_id", "farm-1")
                            .put("name", "North Boundary Fence")
                            .put("section_type", "boundary")
                            .put("section_type_label", "Boundary")
                            .put("paddock_a_id", "paddock-1")
                            .put("paddock_a_name", "North Camp")
                            .put("condition", "unknown")
                            .put("condition_label", "Unknown")
                            .put("height_profile", "low")
                            .put("height_profile_label", "Low")
                            .put("construction_type", "mesh")
                            .put("construction_type_label", "Mesh")
                            .put("electric_wire", false),
                    ),
                )
                .put("fence_events", JSONArray())
                .put(
                    "tasks",
                    JSONArray().put(
                        JSONObject()
                            .put("id", "task-1")
                            .put("display_key", "FIELD-1")
                            .put("heading", "Check water")
                            .put("description", "Confirm tank level")
                            .put("status", "todo")
                            .put("status_label", "To Do")
                            .put("priority", "high")
                            .put("priority_label", "High")
                            .put("due_date", "2026-06-01")
                            .put("comment_count", 0)
                            .put("comments", JSONArray())
                            .put("entity_links", JSONArray()),
                    ),
                )
                .put(
                    "calendar_items",
                    JSONArray().put(
                        JSONObject()
                            .put("kind", "task")
                            .put("date", "2026-06-01")
                            .put("source_id", "task-1")
                            .put("task_id", "task-1")
                            .put("title", "Check water")
                            .put("stage", "todo")
                            .put("stage_label", "To Do")
                            .put("entity_links", JSONArray()),
                    ),
                )
                .put("rainfall", JSONArray())
                .put("mob_events", JSONArray())
                .put("paddock_events", JSONArray())
                .put(
                    "map_features",
                    JSONArray()
                        .put(
                            JSONObject()
                                .put("type", "Feature")
                                .put("geometry", JSONObject().put("type", "Point").put("coordinates", JSONArray().put(25.0).put(-32.0)))
                                .put(
                                    "properties",
                                    JSONObject()
                                        .put("feature_type", "gate")
                                        .put("gate_id", "gate-1")
                                        .put("name", "North Camp / South Camp Gate")
                                        .put("status", "closed"),
                                ),
                        )
                        .put(
                            JSONObject()
                                .put("type", "Feature")
                                .put(
                                    "geometry",
                                    JSONObject()
                                        .put("type", "LineString")
                                        .put(
                                            "coordinates",
                                            JSONArray()
                                                .put(JSONArray().put(25.0).put(-32.0))
                                                .put(JSONArray().put(25.001).put(-32.001)),
                                        ),
                                )
                                .put(
                                    "properties",
                                    JSONObject()
                                        .put("feature_type", "fence_section")
                                        .put("fence_section_id", "fence-1")
                                        .put("name", "North Boundary Fence")
                                        .put("condition", "unknown"),
                                ),
                        ),
                ),
        )

    private fun shearingSnapshot(): FarmSnapshot =
        FarmSnapshot.fromJson(
            JSONObject()
                .put("farm", JSONObject().put("id", "farm-1").put("name", "North Farm").put("timezone", "UTC"))
                .put("shearers", JSONArray())
                .put("shearing_bale_codes", JSONArray())
                .put("shearing_sessions", JSONArray()),
        )

    private fun balance(id: String, mobId: String, groupId: String, headCount: Int): JSONObject =
        JSONObject()
            .put("id", id)
            .put("mob_id", mobId)
            .put("animal_group_type_id", groupId)
            .put("head_count", headCount)
            .put(
                "animal_group_type",
                JSONObject()
                    .put("id", groupId)
                    .put("species", "Cattle")
                    .put("breed", "Bonsmara")
                    .put("sex", "cow")
                    .put("age_class", "adult"),
            )
}
