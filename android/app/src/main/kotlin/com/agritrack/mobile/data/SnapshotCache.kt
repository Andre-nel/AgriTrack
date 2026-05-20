package com.agritrack.mobile.data

import android.content.Context
import android.content.SharedPreferences
import org.json.JSONObject

class SnapshotCache(context: Context) {
    private val prefs: SharedPreferences = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun save(snapshot: FarmSnapshot) {
        prefs.edit()
            .putString(KEY_LAST_FARM_ID, snapshot.farm.id)
            .putString(snapshotKey(snapshot.farm.id), snapshot.rawJson)
            .apply()
    }

    fun loadLast(): FarmSnapshot? {
        val farmId = prefs.getString(KEY_LAST_FARM_ID, null) ?: return null
        return load(farmId)
    }

    fun load(farmId: String): FarmSnapshot? {
        val raw = prefs.getString(snapshotKey(farmId), null) ?: return null
        return runCatching { FarmSnapshot.fromJson(JSONObject(raw)) }.getOrNull()
    }

    private fun snapshotKey(farmId: String): String = "$KEY_SNAPSHOT_PREFIX$farmId"

    private companion object {
        const val PREFS = "agritrack_mobile_snapshot"
        const val KEY_LAST_FARM_ID = "last_farm_id"
        const val KEY_SNAPSHOT_PREFIX = "snapshot_"
    }
}
