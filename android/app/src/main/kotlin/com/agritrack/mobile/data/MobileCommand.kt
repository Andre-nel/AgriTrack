package com.agritrack.mobile.data

import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

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

        fun mobMove(
            farmId: String,
            mobId: String,
            paddockId: String,
            allocationFraction: Double = 1.0,
        ): MobileCommand {
            val payload = JSONObject()
                .put("mob_id", mobId)
                .put(
                    "allocations",
                    JSONArray()
                        .put(
                            JSONObject()
                                .put("paddock_id", paddockId)
                                .put("allocation_fraction", allocationFraction)
                        )
                )
            return MobileCommand(
                clientCommandId = UUID.randomUUID().toString(),
                type = "mob.move",
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
    }
}
