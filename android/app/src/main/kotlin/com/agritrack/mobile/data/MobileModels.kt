package com.agritrack.mobile.data

import org.json.JSONArray
import org.json.JSONObject

data class FarmSummary(
    val id: String,
    val name: String,
    val timezone: String,
)

data class MobSummary(
    val id: String,
    val name: String,
    val status: String,
)

data class FarmSnapshot(
    val farm: FarmSummary,
    val paddockCount: Int,
    val mobCount: Int,
    val waterAssetCount: Int,
    val rainfallCount: Int,
    val mobEventCount: Int,
    val mobs: List<MobSummary>,
    val rawJson: String,
) {
    companion object {
        fun fromJson(json: JSONObject): FarmSnapshot {
            val farmJson = json.getJSONObject("farm")
            val mobsJson = json.optJSONArray("mobs") ?: JSONArray()
            val mobs = buildList {
                for (index in 0 until mobsJson.length()) {
                    val mob = mobsJson.getJSONObject(index)
                    add(
                        MobSummary(
                            id = mob.getString("id"),
                            name = mob.optString("name", "Unnamed mob"),
                            status = mob.optString("status", "unknown"),
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
                paddockCount = json.optJSONArray("paddocks")?.length() ?: 0,
                mobCount = mobsJson.length(),
                waterAssetCount = json.optJSONArray("water_assets")?.length() ?: 0,
                rainfallCount = json.optJSONArray("rainfall")?.length() ?: 0,
                mobEventCount = json.optJSONArray("mob_events")?.length() ?: 0,
                mobs = mobs,
                rawJson = json.toString(),
            )
        }
    }
}

data class BootstrapResult(
    val farms: List<FarmSummary>,
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
            val supported = json
                .optJSONObject("sync")
                ?.optJSONArray("supported_command_types")
                ?: JSONArray()
            return BootstrapResult(
                farms = farms,
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
