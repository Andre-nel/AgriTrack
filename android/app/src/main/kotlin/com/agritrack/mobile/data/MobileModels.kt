package com.agritrack.mobile.data

import org.json.JSONArray
import org.json.JSONObject

data class FarmSummary(
    val id: String,
    val name: String,
    val timezone: String,
)

data class PaddockSummary(
    val id: String,
    val name: String,
    val status: String,
)

data class AnimalGroupTypeSummary(
    val id: String,
    val species: String,
    val breed: String,
    val sex: String,
    val ageClass: String,
) {
    val label: String
        get() = listOf(species, breed, sex, ageClass)
            .filter { it.isNotBlank() }
            .joinToString(" ")
            .ifBlank { "Animal group" }
}

data class MobBalanceSummary(
    val id: String,
    val animalGroupTypeId: String,
    val animalGroupType: AnimalGroupTypeSummary,
    val headCount: Int,
)

data class MobSummary(
    val id: String,
    val name: String,
    val status: String,
    val balances: List<MobBalanceSummary>,
)

data class FarmSnapshot(
    val farm: FarmSummary,
    val paddockCount: Int,
    val mobCount: Int,
    val waterAssetCount: Int,
    val rainfallCount: Int,
    val mobEventCount: Int,
    val paddocks: List<PaddockSummary>,
    val mobs: List<MobSummary>,
    val rawJson: String,
) {
    companion object {
        fun fromJson(json: JSONObject): FarmSnapshot {
            val farmJson = json.getJSONObject("farm")
            val paddocksJson = json.optJSONArray("paddocks") ?: JSONArray()
            val paddocks = buildList {
                for (index in 0 until paddocksJson.length()) {
                    val paddock = paddocksJson.getJSONObject(index)
                    add(
                        PaddockSummary(
                            id = paddock.getString("id"),
                            name = paddock.optString("name", "Unnamed paddock"),
                            status = paddock.optString("status", "active"),
                        )
                    )
                }
            }
            val mobsJson = json.optJSONArray("mobs") ?: JSONArray()
            val mobs = buildList {
                for (index in 0 until mobsJson.length()) {
                    val mob = mobsJson.getJSONObject(index)
                    add(
                        MobSummary(
                            id = mob.getString("id"),
                            name = mob.optString("name", "Unnamed mob"),
                            status = mob.optString("status", "unknown"),
                            balances = parseBalances(mob.optJSONArray("balances") ?: JSONArray()),
                        )
                    )
                }
            }
            return FarmSnapshot(
                farm = FarmSummary(
                    id = farmJson.getString("id"),
                    name = farmJson.optString("name", "Farm"),
                    timezone = farmJson.optString("timezone", "UTC"),
                ),
                paddockCount = paddocksJson.length(),
                mobCount = mobsJson.length(),
                waterAssetCount = json.optJSONArray("water_assets")?.length() ?: 0,
                rainfallCount = json.optJSONArray("rainfall")?.length() ?: 0,
                mobEventCount = json.optJSONArray("mob_events")?.length() ?: 0,
                paddocks = paddocks,
                mobs = mobs,
                rawJson = json.toString(),
            )
        }

        private fun parseBalances(json: JSONArray): List<MobBalanceSummary> = buildList {
            for (index in 0 until json.length()) {
                val balance = json.getJSONObject(index)
                val groupJson = balance.optJSONObject("animal_group_type") ?: JSONObject()
                val groupId = balance.optString(
                    "animal_group_type_id",
                    groupJson.optString("id"),
                )
                add(
                    MobBalanceSummary(
                        id = balance.optString("id"),
                        animalGroupTypeId = groupId,
                        animalGroupType = parseAnimalGroupType(groupJson, groupId),
                        headCount = balance.optInt("head_count", 0),
                    )
                )
            }
        }
    }
}

data class BootstrapResult(
    val farms: List<FarmSummary>,
    val animalGroupTypes: List<AnimalGroupTypeSummary>,
    val supportedCommandTypes: List<String>,
) {
    companion object {
        fun fromJson(json: JSONObject): BootstrapResult {
            val farmsJson = json.optJSONArray("farms") ?: JSONArray()
            val farms = buildList {
                for (index in 0 until farmsJson.length()) {
                    val farm = farmsJson.getJSONObject(index)
                    add(
                        FarmSummary(
                            id = farm.getString("id"),
                            name = farm.optString("name", "Farm"),
                            timezone = farm.optString("timezone", "UTC"),
                        )
                    )
                }
            }
            val groupTypesJson = json.optJSONArray("animal_group_types") ?: JSONArray()
            val groupTypes = buildList {
                for (index in 0 until groupTypesJson.length()) {
                    add(parseAnimalGroupType(groupTypesJson.getJSONObject(index)))
                }
            }
            val supported = json
                .optJSONObject("sync")
                ?.optJSONArray("supported_command_types")
                ?: JSONArray()
            return BootstrapResult(
                farms = farms,
                animalGroupTypes = groupTypes,
                supportedCommandTypes = buildList {
                    for (index in 0 until supported.length()) {
                        add(supported.getString(index))
                    }
                },
            )
        }
    }
}

data class SyncResult(
    val clientCommandId: String,
    val type: String,
    val status: String,
    val duplicate: Boolean,
    val errorMessage: String?,
) {
    companion object {
        fun fromJson(json: JSONObject): SyncResult {
            val error = json.optJSONObject("error")
            return SyncResult(
                clientCommandId = json.optString("client_command_id"),
                type = json.optString("type"),
                status = json.optString("status"),
                duplicate = json.optBoolean("duplicate", false),
                errorMessage = error?.optString("message"),
            )
        }
    }
}

data class SyncSummary(
    val results: List<SyncResult>,
    val remainingQueueCount: Int,
) {
    val appliedCount: Int = results.count { it.status == "applied" }
    val failedCount: Int = results.count { it.status == "failed" }
}

data class LoginLoadResult(
    val bootstrap: BootstrapResult,
    val snapshot: FarmSnapshot?,
)

private fun parseAnimalGroupType(json: JSONObject, fallbackId: String = ""): AnimalGroupTypeSummary =
    AnimalGroupTypeSummary(
        id = json.optString("id", fallbackId),
        species = json.optString("species"),
        breed = json.optString("breed"),
        sex = json.optString("sex"),
        ageClass = json.optString("age_class"),
    )
