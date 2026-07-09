package com.agritrack.mobile.data

import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

data class MobMoveGroupCount(
    val animalGroupTypeId: String,
    val headCount: Int,
)

data class MobMoveCountAllocation(
    val paddockId: String,
    val groupCounts: List<MobMoveGroupCount>,
)

data class MobileCommand(
    val clientCommandId: String,
    val type: String,
    val farmId: String,
    val payload: JSONObject,
) {
    fun toJson(): JSONObject = JSONObject()
        .put("client_command_id", clientCommandId)
        .put("type", type)
        .put("farm_id", farmId)
        .put("payload", payload)

    companion object {
        fun fromJson(json: JSONObject): MobileCommand = MobileCommand(
            clientCommandId = json.getString("client_command_id"),
            type = json.getString("type"),
            farmId = json.getString("farm_id"),
            payload = json.getJSONObject("payload"),
        )

        fun rainfall(
            farmId: String,
            recordedOn: String,
            mm: Double,
            note: String,
        ): MobileCommand {
            val payload = JSONObject()
                .put("recorded_on", recordedOn)
                .put("mm", mm)
                .put("source", "mobile")
            if (note.isNotBlank()) {
                payload.put("note", note.trim())
            }
            return MobileCommand(
                clientCommandId = UUID.randomUUID().toString(),
                type = "rainfall.create",
                farmId = farmId,
                payload = payload,
            )
        }

        fun mobNote(
            farmId: String,
            mobId: String,
            description: String,
            tags: List<String>,
        ): MobileCommand {
            val payload = JSONObject()
                .put("mob_id", mobId)
                .put("description", description.trim())
                .put("tags", JSONArray(tags))
            return MobileCommand(
                clientCommandId = UUID.randomUUID().toString(),
                type = "mob_event.create",
                farmId = farmId,
                payload = payload,
            )
        }

        fun mobCreate(
            farmId: String,
            name: String,
            originNote: String,
        ): MobileCommand {
            val payload = JSONObject()
                .put("name", name.trim())
            if (originNote.isNotBlank()) {
                payload.put("origin_note", originNote.trim())
            }
            return MobileCommand(
                clientCommandId = UUID.randomUUID().toString(),
                type = "mob.create",
                farmId = farmId,
                payload = payload,
            )
        }

        fun mobMove(
            farmId: String,
            mobId: String,
            paddockId: String,
            allocationFraction: Double = 1.0,
        ): MobileCommand = mobMoveAllocations(
            farmId = farmId,
            mobId = mobId,
            allocations = listOf(paddockId to allocationFraction),
        )

        fun mobMoveAllocations(
            farmId: String,
            mobId: String,
            allocations: List<Pair<String, Double>>,
        ): MobileCommand {
            val payload = JSONObject()
                .put("mob_id", mobId)
                .put(
                    "allocations",
                    JSONArray().apply {
                        allocations.forEach { (paddockId, allocationFraction) ->
                            put(
                                JSONObject()
                                    .put("paddock_id", paddockId)
                                    .put("allocation_fraction", allocationFraction)
                            )
                        }
                    }
                )
            return MobileCommand(
                clientCommandId = UUID.randomUUID().toString(),
                type = "mob.move",
                farmId = farmId,
                payload = payload,
            )
        }

        fun mobMoveCountAllocations(
            farmId: String,
            mobId: String,
            allocations: List<MobMoveCountAllocation>,
        ): MobileCommand {
            val payload = JSONObject()
                .put("mob_id", mobId)
                .put("allocation_mode", "counts")
                .put(
                    "allocations",
                    JSONArray().apply {
                        allocations.forEach { allocation ->
                            put(
                                JSONObject()
                                    .put("paddock_id", allocation.paddockId)
                                    .put(
                                        "group_counts",
                                        JSONArray().apply {
                                            allocation.groupCounts.forEach { group ->
                                                put(
                                                    JSONObject()
                                                        .put("animal_group_type_id", group.animalGroupTypeId)
                                                        .put("head_count", group.headCount)
                                                )
                                            }
                                        }
                                    )
                            )
                        }
                    }
                )
            return MobileCommand(
                clientCommandId = UUID.randomUUID().toString(),
                type = "mob.move",
                farmId = farmId,
                payload = payload,
            )
        }

        fun paddockNote(
            farmId: String,
            paddockId: String,
            description: String,
            tags: List<String>,
        ): MobileCommand {
            val payload = JSONObject()
                .put("paddock_id", paddockId)
                .put("description", description.trim())
                .put("tags", JSONArray(tags))
            return MobileCommand(
                clientCommandId = UUID.randomUUID().toString(),
                type = "paddock_event.create",
                farmId = farmId,
                payload = payload,
            )
        }

        fun waterAssetNote(
            farmId: String,
            waterAssetId: String,
            description: String,
            tags: List<String>,
        ): MobileCommand {
            val payload = JSONObject()
                .put("water_asset_id", waterAssetId)
                .put("description", description.trim())
                .put("tags", JSONArray(tags))
            return MobileCommand(
                clientCommandId = UUID.randomUUID().toString(),
                type = "water_asset_event.create",
                farmId = farmId,
                payload = payload,
            )
        }

        fun fenceNote(
            farmId: String,
            fenceSectionId: String,
            description: String,
            tags: List<String>,
            eventType: String = "inspection",
            conditionAfter: String = "",
        ): MobileCommand {
            val payload = JSONObject()
                .put("fence_section_id", fenceSectionId)
                .put("event_type", eventType.ifBlank { "inspection" })
                .put("description", description.trim())
                .put("tags", JSONArray(tags))
            if (conditionAfter.isNotBlank()) {
                payload.put("condition_after", conditionAfter.trim())
            }
            return MobileCommand(
                clientCommandId = UUID.randomUUID().toString(),
                type = "fence_event.create",
                farmId = farmId,
                payload = payload,
            )
        }

        fun incidentCreate(
            farmId: String,
            occurredOn: String,
            category: String,
            note: String,
            tags: List<String>,
            reportedBy: String = "Mobile user",
        ): MobileCommand {
            val payload = JSONObject()
                .put("occurred_on", occurredOn.trim())
                .put("category", category.trim())
                .put("note", note.trim())
                .put("tags", JSONArray(tags))
                .put("reported_by", reportedBy.trim().ifBlank { "Mobile user" })
            return MobileCommand(
                clientCommandId = UUID.randomUUID().toString(),
                type = "incident.create",
                farmId = farmId,
                payload = payload,
            )
        }

        fun stockCount(
            farmId: String,
            mobId: String,
            animalGroupTypeId: String,
            quantity: Int,
            note: String,
        ): MobileCommand {
            val payload = JSONObject()
                .put("mob_id", mobId)
                .put("animal_group_type_id", animalGroupTypeId)
                .put("quantity", quantity)
            if (note.isNotBlank()) {
                payload.put("note", note.trim())
            }
            return MobileCommand(
                clientCommandId = UUID.randomUUID().toString(),
                type = "stock_count.record",
                farmId = farmId,
                payload = payload,
            )
        }

        fun stockCount(
            farmId: String,
            mobId: String,
            species: String,
            breed: String,
            sex: String,
            ageClass: String,
            quantity: Int,
            note: String,
        ): MobileCommand {
            val payload = JSONObject()
                .put("mob_id", mobId)
                .put(
                    "animal_group_type",
                    JSONObject()
                        .put("species", species.trim())
                        .put("breed", breed.trim())
                        .put("sex", sex.trim())
                        .put("age_class", ageClass.trim())
                )
                .put("quantity", quantity)
            if (note.isNotBlank()) {
                payload.put("note", note.trim())
            }
            return MobileCommand(
                clientCommandId = UUID.randomUUID().toString(),
                type = "stock_count.record",
                farmId = farmId,
                payload = payload,
            )
        }

        fun waterAssetStatus(
            farmId: String,
            waterAssetId: String,
            status: String,
            waterLevel: String,
            active: Boolean = true,
        ): MobileCommand {
            val payload = JSONObject()
                .put("water_asset_id", waterAssetId)
                .put("active", active)
            if (status.isNotBlank()) {
                payload.put("status", status.trim())
            }
            if (waterLevel.isNotBlank()) {
                payload.put("water_level", waterLevel.trim())
            }
            return MobileCommand(
                clientCommandId = UUID.randomUUID().toString(),
                type = "water_asset_status.update",
                farmId = farmId,
                payload = payload,
            )
        }

        fun fenceUpdate(
            farmId: String,
            fenceSectionId: String,
            condition: String,
            notes: String,
            electricWire: Boolean,
        ): MobileCommand {
            val payload = JSONObject()
                .put("fence_section_id", fenceSectionId)
                .put("electric_wire", electricWire)
            if (condition.isNotBlank()) {
                payload.put("condition", condition.trim())
            }
            payload.put("notes", notes.trim())
            return MobileCommand(
                clientCommandId = UUID.randomUUID().toString(),
                type = "fence_section.update",
                farmId = farmId,
                payload = payload,
            )
        }

        fun paddockUpdate(
            farmId: String,
            paddockId: String,
            status: String,
            notes: String,
            tags: String,
        ): MobileCommand {
            val payload = JSONObject()
                .put("paddock_id", paddockId)
            if (status.isNotBlank()) {
                payload.put("status", status.trim())
            }
            payload.put("notes", notes.trim())
            payload.put(
                "tags",
                JSONArray(
                    tags.split(",", ";", "\n")
                        .map { it.trim() }
                        .filter { it.isNotBlank() }
                )
            )
            return MobileCommand(
                clientCommandId = UUID.randomUUID().toString(),
                type = "paddock.update",
                farmId = farmId,
                payload = payload,
            )
        }

        fun gateUpdate(
            farmId: String,
            gateId: String,
            status: String,
            closureChoices: List<Pair<String, String>> = emptyList(),
            eventTime: String? = null,
        ): MobileCommand {
            val payload = JSONObject()
                .put("gate_id", gateId)
                .put("status", status.trim())
                .put(
                    "closure_choices",
                    JSONArray().apply {
                        closureChoices.forEach { (mobId, componentPaddockId) ->
                            put(
                                JSONObject()
                                    .put("mob_id", mobId)
                                    .put("component_paddock_id", componentPaddockId)
                            )
                        }
                    }
                )
            if (!eventTime.isNullOrBlank()) {
                payload.put("event_time", eventTime.trim())
            }
            return MobileCommand(
                clientCommandId = UUID.randomUUID().toString(),
                type = "gate.update",
                farmId = farmId,
                payload = payload,
            )
        }

        fun taskCreate(
            farmId: String,
            heading: String,
            description: String,
            dueDate: String,
            entityType: String,
            entityId: String,
        ): MobileCommand {
            val payload = JSONObject()
                .put("heading", heading.trim())
                .put("description", description.trim().ifBlank { heading.trim() })
                .put("priority", "high")
                .put("reporter_name", "Mobile user")
            if (dueDate.isNotBlank()) {
                payload.put("due_date", dueDate.trim())
            }
            when (entityType) {
                "paddock" -> payload.put("paddock_id", entityId)
                "mob" -> payload.put("mob_id", entityId)
                "water_asset" -> payload.put("water_asset_id", entityId)
                "fence_section" -> payload.put("fence_section_id", entityId)
            }
            return MobileCommand(
                clientCommandId = UUID.randomUUID().toString(),
                type = "task.create",
                farmId = farmId,
                payload = payload,
            )
        }

        fun taskStatus(
            farmId: String,
            taskId: String,
            status: String,
            note: String,
        ): MobileCommand {
            val payload = JSONObject()
                .put("task_id", taskId)
                .put("status", status)
                .put("changed_by_name", "Mobile user")
            if (note.isNotBlank()) {
                payload.put("note", note.trim())
            }
            return MobileCommand(
                clientCommandId = UUID.randomUUID().toString(),
                type = "task.status.update",
                farmId = farmId,
                payload = payload,
            )
        }

        fun taskComment(
            farmId: String,
            taskId: String,
            body: String,
        ): MobileCommand = MobileCommand(
            clientCommandId = UUID.randomUUID().toString(),
            type = "task.comment.create",
            farmId = farmId,
            payload = JSONObject()
                .put("task_id", taskId)
                .put("author_name", "Mobile user")
                .put("body", body.trim()),
        )

        fun mobTransfer(
            farmId: String,
            sourceMobId: String,
            destinationMobId: String,
            animalGroupTypeId: String,
            quantity: Int,
            note: String,
        ): MobileCommand {
            val payload = JSONObject()
                .put("source_mob_id", sourceMobId)
                .put("destination_mob_id", destinationMobId)
                .put(
                    "transfers",
                    JSONArray()
                        .put(
                            JSONObject()
                                .put("animal_group_type_id", animalGroupTypeId)
                                .put("quantity", quantity)
                        )
                )
            if (note.isNotBlank()) {
                payload.put("note", note.trim())
            }
            return MobileCommand(
                clientCommandId = UUID.randomUUID().toString(),
                type = "mob.transfer",
                farmId = farmId,
                payload = payload,
            )
        }

        fun shearerCreate(
            farmId: String,
            shearerId: String,
            name: String,
        ): MobileCommand {
            val payload = JSONObject()
                .put("id", shearerId)
                .put("name", name.trim())
                .put("active", true)
            return MobileCommand(
                clientCommandId = UUID.randomUUID().toString(),
                type = "shearer.create",
                farmId = farmId,
                payload = payload,
            )
        }

        fun shearingSessionCreate(
            farmId: String,
            sessionId: String,
            name: String,
            species: String,
            startDate: String,
            endDate: String,
            lootjieRate: Double,
            notes: String,
        ): MobileCommand {
            val payload = JSONObject()
                .put("id", sessionId)
                .put("name", name.trim())
                .put("species", species.trim())
                .put("start_date", startDate.trim())
                .put("lootjie_rate", lootjieRate)
            if (endDate.isNotBlank()) {
                payload.put("end_date", endDate.trim())
            }
            if (notes.isNotBlank()) {
                payload.put("notes", notes.trim())
            }
            return MobileCommand(
                clientCommandId = UUID.randomUUID().toString(),
                type = "shearing_session.create",
                farmId = farmId,
                payload = payload,
            )
        }

        fun shearingSessionUpdate(
            farmId: String,
            sessionId: String,
            status: String,
        ): MobileCommand = MobileCommand(
            clientCommandId = UUID.randomUUID().toString(),
            type = "shearing_session.update",
            farmId = farmId,
            payload = JSONObject()
                .put("session_id", sessionId)
                .put("status", status.trim()),
        )

        fun shearingEntryRecord(
            farmId: String,
            entryId: String,
            sessionId: String,
            workDate: String,
            shearerId: String,
            animalGroupType: AnimalGroupTypeSummary,
            quantity: Int,
            note: String,
        ): MobileCommand {
            val payload = JSONObject()
                .put("id", entryId)
                .put("session_id", sessionId)
                .put("work_date", workDate.trim())
                .put("shearer_id", shearerId)
                .put(
                    "animal_group_type",
                    JSONObject()
                        .put("id", animalGroupType.id)
                        .put("species", animalGroupType.species)
                        .put("breed", animalGroupType.breed)
                        .put("sex", animalGroupType.sex)
                        .put("age_class", animalGroupType.ageClass)
                )
                .put("quantity", quantity)
            if (animalGroupType.id.isNotBlank() && !animalGroupType.id.startsWith("pending-")) {
                payload.put("animal_group_type_id", animalGroupType.id)
            }
            if (note.isNotBlank()) {
                payload.put("note", note.trim())
            }
            return MobileCommand(
                clientCommandId = UUID.randomUUID().toString(),
                type = "shearing_entry.record",
                farmId = farmId,
                payload = payload,
            )
        }

        fun shearingEntryDelete(
            farmId: String,
            sessionId: String,
            entryId: String,
        ): MobileCommand = MobileCommand(
            clientCommandId = UUID.randomUUID().toString(),
            type = "shearing_entry.delete",
            farmId = farmId,
            payload = JSONObject()
                .put("session_id", sessionId)
                .put("entry_id", entryId),
        )

        fun shearingBaleCodeUpsert(
            farmId: String,
            baleCodeId: String,
            species: String,
            code: String,
            lineType: String,
            ageGroup: String,
            finenessGrade: String,
            lengthCode: String,
            finenessMicron: Double?,
            cleanYieldPercent: Double?,
            color: String,
            vegetableMatter: String,
            styleCharacter: String,
            consistency: String,
            fault: String,
            description: String,
            notes: String,
        ): MobileCommand {
            val payload = JSONObject()
                .put("id", baleCodeId)
                .put("species", species.trim())
                .put("code", code.trim())
                .put("active", true)
            if (lineType.isNotBlank()) payload.put("line_type", lineType.trim())
            if (ageGroup.isNotBlank()) payload.put("age_group", ageGroup.trim())
            if (finenessGrade.isNotBlank()) payload.put("fineness_grade", finenessGrade.trim())
            if (lengthCode.isNotBlank()) payload.put("length_code", lengthCode.trim().uppercase())
            finenessMicron?.let { payload.put("fineness_micron", it) }
            cleanYieldPercent?.let { payload.put("clean_yield_percent", it) }
            if (color.isNotBlank()) payload.put("color", color.trim())
            if (vegetableMatter.isNotBlank()) payload.put("vegetable_matter", vegetableMatter.trim())
            if (styleCharacter.isNotBlank()) payload.put("style_character", styleCharacter.trim())
            if (consistency.isNotBlank()) payload.put("consistency", consistency.trim())
            if (fault.isNotBlank()) payload.put("fault", fault.trim())
            if (description.isNotBlank()) payload.put("description", description.trim())
            if (notes.isNotBlank()) payload.put("notes", notes.trim())
            return MobileCommand(
                clientCommandId = UUID.randomUUID().toString(),
                type = "shearing_bale_code.upsert",
                farmId = farmId,
                payload = payload,
            )
        }

        fun shearingBaleRecord(
            farmId: String,
            baleId: String,
            sessionId: String,
            baleCodeId: String?,
            codeText: String,
            baleNumber: String,
            weightKg: Double,
            pricePerKg: Double?,
            totalPrice: Double?,
            notes: String,
        ): MobileCommand {
            val payload = JSONObject()
                .put("id", baleId)
                .put("session_id", sessionId)
                .put("weight_kg", weightKg)
            baleCodeId?.takeIf { it.isNotBlank() }?.let { payload.put("bale_code_id", it) }
            if (codeText.isNotBlank()) payload.put("code_text", codeText.trim())
            if (baleNumber.isNotBlank()) payload.put("bale_number", baleNumber.trim())
            pricePerKg?.let { payload.put("price_per_kg", it) }
            totalPrice?.let { payload.put("total_price", it) }
            if (notes.isNotBlank()) payload.put("notes", notes.trim())
            return MobileCommand(
                clientCommandId = UUID.randomUUID().toString(),
                type = "shearing_bale.record",
                farmId = farmId,
                payload = payload,
            )
        }

        fun shearingBaleDelete(
            farmId: String,
            sessionId: String,
            baleId: String,
        ): MobileCommand = MobileCommand(
            clientCommandId = UUID.randomUUID().toString(),
            type = "shearing_bale.delete",
            farmId = farmId,
            payload = JSONObject()
                .put("session_id", sessionId)
                .put("bale_id", baleId),
        )
    }
}
