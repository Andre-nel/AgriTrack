package com.agritrack.mobile.data

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import org.json.JSONArray
import org.json.JSONObject

class LocalFieldStore(context: Context) : SQLiteOpenHelper(context, DB_NAME, null, DB_VERSION) {
    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE snapshots (
                farm_id TEXT PRIMARY KEY,
                raw_json TEXT NOT NULL,
                saved_at INTEGER NOT NULL
            )
            """.trimIndent()
        )
        db.execSQL(
            """
            CREATE TABLE entities (
                farm_id TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                saved_at INTEGER NOT NULL,
                PRIMARY KEY (farm_id, entity_type, entity_id)
            )
            """.trimIndent()
        )
        db.execSQL(
            """
            CREATE TABLE outbox (
                client_command_id TEXT PRIMARY KEY,
                command_type TEXT NOT NULL,
                farm_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                retry_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT
            )
            """.trimIndent()
        )
        db.execSQL(
            """
            CREATE TABLE settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """.trimIndent()
        )
        db.execSQL("CREATE INDEX idx_entities_farm_type ON entities(farm_id, entity_type)")
        db.execSQL("CREATE INDEX idx_outbox_status ON outbox(status)")
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        db.execSQL("DROP TABLE IF EXISTS settings")
        db.execSQL("DROP TABLE IF EXISTS outbox")
        db.execSQL("DROP TABLE IF EXISTS entities")
        db.execSQL("DROP TABLE IF EXISTS snapshots")
        onCreate(db)
    }

    fun saveSnapshot(snapshot: FarmSnapshot) {
        val now = System.currentTimeMillis()
        writableDatabase.beginTransaction()
        try {
            writableDatabase.insertWithOnConflict(
                "snapshots",
                null,
                ContentValues().apply {
                    put("farm_id", snapshot.farm.id)
                    put("raw_json", snapshot.rawJson)
                    put("saved_at", now)
                },
                SQLiteDatabase.CONFLICT_REPLACE,
            )
            writableDatabase.delete("entities", "farm_id = ?", arrayOf(snapshot.farm.id))
            val json = JSONObject(snapshot.rawJson)
            saveEntity(snapshot.farm.id, "farm", snapshot.farm.id, json.getJSONObject("farm"), now)
            saveArray(snapshot.farm.id, "paddock", json.optJSONArray("paddocks"), "id", now)
            saveArray(snapshot.farm.id, "mob", json.optJSONArray("mobs"), "id", now)
            saveArray(snapshot.farm.id, "active_grazing", json.optJSONArray("active_grazing"), "id", now)
            saveArray(snapshot.farm.id, "water_asset", json.optJSONArray("water_assets"), "id", now)
            saveArray(snapshot.farm.id, "rainfall", json.optJSONArray("rainfall"), "id", now)
            saveArray(snapshot.farm.id, "task", json.optJSONArray("tasks"), "id", now)
            saveArray(snapshot.farm.id, "calendar_item", json.optJSONArray("calendar_items"), "date", now)
            saveArray(snapshot.farm.id, "decision", json.optJSONArray("decision_feed"), "title", now)
            saveArray(snapshot.farm.id, "map_feature", json.optJSONArray("map_features"), "properties.name", now)
            setSetting("last_farm_id", snapshot.farm.id)
            writableDatabase.setTransactionSuccessful()
        } finally {
            writableDatabase.endTransaction()
        }
    }

    fun loadLastSnapshot(): FarmSnapshot? {
        val farmId = getSetting("last_farm_id") ?: return null
        return loadSnapshot(farmId)
    }

    fun loadSnapshot(farmId: String): FarmSnapshot? =
        readableDatabase.rawQuery(
            "SELECT raw_json FROM snapshots WHERE farm_id = ?",
            arrayOf(farmId),
        ).use { cursor ->
            if (!cursor.moveToFirst()) {
                null
            } else {
                runCatching { FarmSnapshot.fromJson(JSONObject(cursor.getString(0))) }.getOrNull()
            }
        }

    fun enqueue(command: MobileCommand) {
        writableDatabase.insertWithOnConflict(
            "outbox",
            null,
            ContentValues().apply {
                put("client_command_id", command.clientCommandId)
                put("command_type", command.type)
                put("farm_id", command.farmId)
                put("payload", command.toJson().toString())
                put("status", "pending")
                put("created_at", System.currentTimeMillis())
                put("retry_count", 0)
            },
            SQLiteDatabase.CONFLICT_IGNORE,
        )
    }

    fun pendingJson(): JSONArray {
        val array = JSONArray()
        readableDatabase.rawQuery(
            """
            SELECT payload FROM outbox
            WHERE status IN ('pending', 'failed')
            ORDER BY created_at ASC
            """.trimIndent(),
            emptyArray(),
        ).use { cursor ->
            while (cursor.moveToNext()) {
                array.put(JSONObject(cursor.getString(0)))
            }
        }
        return array
    }

    fun pendingCount(): Int = countOutbox("pending") + countOutbox("failed")

    fun failedCommands(limit: Int = 20): List<OutboxFailure> {
        val failures = mutableListOf<OutboxFailure>()
        readableDatabase.rawQuery(
            """
            SELECT client_command_id, command_type, last_error, retry_count
            FROM outbox
            WHERE status = 'failed'
            ORDER BY created_at ASC
            LIMIT ?
            """.trimIndent(),
            arrayOf(limit.toString()),
        ).use { cursor ->
            while (cursor.moveToNext()) {
                failures.add(
                    OutboxFailure(
                        clientCommandId = cursor.getString(0),
                        type = cursor.getString(1),
                        lastError = cursor.getString(2),
                        retryCount = cursor.getInt(3),
                    )
                )
            }
        }
        return failures
    }

    fun applySyncResults(results: JSONArray) {
        writableDatabase.beginTransaction()
        try {
            for (index in 0 until results.length()) {
                val result = results.getJSONObject(index)
                val id = result.optString("client_command_id")
                if (id.isBlank()) {
                    continue
                }
                if (SyncOutboxPolicy.shouldDrop(result)) {
                    writableDatabase.delete("outbox", "client_command_id = ?", arrayOf(id))
                } else {
                    val message = result.optJSONObject("error")?.optString("message") ?: "Sync failed"
                    writableDatabase.update(
                        "outbox",
                        ContentValues().apply {
                            put("status", "failed")
                            put("last_error", message)
                            put("retry_count", retryCount(id) + 1)
                        },
                        "client_command_id = ?",
                        arrayOf(id),
                    )
                }
            }
            writableDatabase.setTransactionSuccessful()
        } finally {
            writableDatabase.endTransaction()
        }
    }

    fun getSyncIntervalMinutes(): Int =
        getSetting("sync_interval_minutes")?.toIntOrNull()?.coerceAtLeast(1) ?: DEFAULT_SYNC_INTERVAL_MINUTES

    fun setSyncIntervalMinutes(value: Int) {
        setSetting("sync_interval_minutes", value.coerceAtLeast(1).toString())
    }

    private fun saveArray(
        farmId: String,
        entityType: String,
        array: JSONArray?,
        idField: String,
        savedAt: Long,
    ) {
        if (array == null) {
            return
        }
        for (index in 0 until array.length()) {
            val item = array.getJSONObject(index)
            val id = when (idField) {
                "properties.name" -> item.optJSONObject("properties")?.optString("name")
                else -> item.optString(idField)
            }.orEmpty().ifBlank { "$entityType-$index" }
            saveEntity(farmId, entityType, id, item, savedAt)
        }
    }

    private fun saveEntity(
        farmId: String,
        entityType: String,
        entityId: String,
        payload: JSONObject,
        savedAt: Long,
    ) {
        writableDatabase.insertWithOnConflict(
            "entities",
            null,
            ContentValues().apply {
                put("farm_id", farmId)
                put("entity_type", entityType)
                put("entity_id", entityId)
                put("payload", payload.toString())
                put("saved_at", savedAt)
            },
            SQLiteDatabase.CONFLICT_REPLACE,
        )
    }

    private fun countOutbox(status: String): Int =
        readableDatabase.rawQuery(
            "SELECT COUNT(*) FROM outbox WHERE status = ?",
            arrayOf(status),
        ).use { cursor ->
            if (cursor.moveToFirst()) cursor.getInt(0) else 0
        }

    private fun retryCount(clientCommandId: String): Int =
        readableDatabase.rawQuery(
            "SELECT retry_count FROM outbox WHERE client_command_id = ?",
            arrayOf(clientCommandId),
        ).use { cursor ->
            if (cursor.moveToFirst()) cursor.getInt(0) else 0
        }

    private fun getSetting(key: String): String? =
        readableDatabase.rawQuery(
            "SELECT value FROM settings WHERE key = ?",
            arrayOf(key),
        ).use { cursor ->
            if (cursor.moveToFirst()) cursor.getString(0) else null
        }

    private fun setSetting(key: String, value: String) {
        writableDatabase.insertWithOnConflict(
            "settings",
            null,
            ContentValues().apply {
                put("key", key)
                put("value", value)
            },
            SQLiteDatabase.CONFLICT_REPLACE,
        )
    }

    private companion object {
        const val DB_NAME = "agritrack_field_store.db"
        const val DB_VERSION = 1
        const val DEFAULT_SYNC_INTERVAL_MINUTES = 5
    }
}

data class OutboxFailure(
    val clientCommandId: String,
    val type: String,
    val lastError: String?,
    val retryCount: Int,
)
