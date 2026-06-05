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
        createTaskPhotoOutbox(db)
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        if (oldVersion < 2) {
            createTaskPhotoOutbox(db)
            return
        }
        db.execSQL("DROP TABLE IF EXISTS settings")
        db.execSQL("DROP TABLE IF EXISTS task_photo_outbox")
        db.execSQL("DROP TABLE IF EXISTS outbox")
        db.execSQL("DROP TABLE IF EXISTS entities")
        db.execSQL("DROP TABLE IF EXISTS snapshots")
        onCreate(db)
    }

    fun saveSnapshot(snapshot: FarmSnapshot, makeActive: Boolean = true) {
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
            saveArray(snapshot.farm.id, "mob_event", json.optJSONArray("mob_events"), "id", now)
            saveArray(snapshot.farm.id, "paddock_event", json.optJSONArray("paddock_events"), "id", now)
            saveArray(snapshot.farm.id, "water_asset_event", json.optJSONArray("water_asset_events"), "id", now)
            saveArray(snapshot.farm.id, "water_asset_state_history", json.optJSONArray("water_asset_state_history"), "id", now)
            saveArray(snapshot.farm.id, "task", json.optJSONArray("tasks"), "id", now)
            saveArray(snapshot.farm.id, "calendar_item", json.optJSONArray("calendar_items"), "date", now)
            saveArray(snapshot.farm.id, "decision", json.optJSONArray("decision_feed"), "title", now)
            saveArray(snapshot.farm.id, "map_feature", json.optJSONArray("map_features"), "properties.name", now)
            if (makeActive) {
                setActiveFarmId(snapshot.farm.id)
            }
            writableDatabase.setTransactionSuccessful()
        } finally {
            writableDatabase.endTransaction()
        }
    }

    fun loadLastSnapshot(): FarmSnapshot? {
        val farmId = loadLastFarmId() ?: return null
        return loadSnapshot(farmId)
    }

    fun loadLastFarmId(): String? = getSetting("last_farm_id")?.takeIf { it.isNotBlank() }

    fun setActiveFarmId(farmId: String) {
        if (farmId.isNotBlank()) {
            setSetting("last_farm_id", farmId)
        }
    }

    fun clearActiveFarmId() {
        setSetting("last_farm_id", "")
    }

    fun loadBaseUrl(): String? = getSetting("base_url")?.takeIf { it.isNotBlank() }

    fun saveBaseUrl(baseUrl: String) {
        val normalized = normalizeBaseUrl(baseUrl)
        if (normalized.isNotBlank()) {
            setSetting("base_url", normalized)
        }
    }

    fun saveAvailableFarms(farms: List<FarmSummary>) {
        val array = JSONArray()
        farms.forEach { farm -> array.put(farm.toJson()) }
        setSetting("available_farms", array.toString())
    }

    fun loadAvailableFarms(): List<FarmSummary> {
        val raw = getSetting("available_farms") ?: return emptyList()
        return runCatching {
            val array = JSONArray(raw)
            buildList {
                for (index in 0 until array.length()) {
                    add(FarmSummary.fromJson(array.getJSONObject(index)))
                }
            }
        }.getOrDefault(emptyList())
    }

    fun loadSnapshot(farmId: String): FarmSnapshot? {
        val snapshot = readableDatabase.rawQuery(
            "SELECT raw_json FROM snapshots WHERE farm_id = ?",
            arrayOf(farmId),
        ).use { cursor ->
            if (!cursor.moveToFirst()) {
                null
            } else {
                runCatching { FarmSnapshot.fromJson(JSONObject(cursor.getString(0))) }.getOrNull()
            }
        }
        return snapshot
            ?.withOptimisticCommands(pendingJsonForFarm(farmId))
            ?.withOptimisticTaskPhotos(pendingTaskPhotoJsonForFarm(farmId))
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

    private fun pendingJsonForFarm(farmId: String): JSONArray {
        val array = JSONArray()
        readableDatabase.rawQuery(
            """
            SELECT payload FROM outbox
            WHERE farm_id = ? AND status IN ('pending', 'failed')
            ORDER BY created_at ASC
            """.trimIndent(),
            arrayOf(farmId),
        ).use { cursor ->
            while (cursor.moveToNext()) {
                array.put(JSONObject(cursor.getString(0)))
            }
        }
        return array
    }

    private fun pendingTaskPhotoJsonForFarm(farmId: String): JSONArray {
        val array = JSONArray()
        readableDatabase.rawQuery(
            """
            SELECT client_attachment_id, farm_id, server_task_id, target_client_command_id,
                   original_filename, content_type, byte_size, caption, captured_at
            FROM task_photo_outbox
            WHERE farm_id = ? AND status IN ('pending', 'failed')
            ORDER BY created_at ASC
            """.trimIndent(),
            arrayOf(farmId),
        ).use { cursor ->
            while (cursor.moveToNext()) {
                array.put(
                    JSONObject()
                        .put("client_attachment_id", cursor.getString(0))
                        .put("farm_id", cursor.getString(1))
                        .putNullable("server_task_id", if (cursor.isNull(2)) null else cursor.getString(2))
                        .putNullable("target_client_command_id", if (cursor.isNull(3)) null else cursor.getString(3))
                        .put("original_filename", cursor.getString(4))
                        .put("content_type", cursor.getString(5))
                        .put("byte_size", cursor.getLong(6))
                        .putNullable("caption", if (cursor.isNull(7)) null else cursor.getString(7))
                        .putNullable("captured_at", if (cursor.isNull(8)) null else cursor.getString(8))
                )
            }
        }
        return array
    }

    fun pendingCount(): Int = countOutbox("pending") + countOutbox("failed") + pendingTaskPhotoCount()

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

    fun resolveTaskPhotoTargets(results: JSONArray) {
        writableDatabase.beginTransaction()
        try {
            for (index in 0 until results.length()) {
                val result = results.getJSONObject(index)
                if (result.optString("status") != "applied" || result.optString("type") != "task.create") {
                    continue
                }
                val clientCommandId = result.optString("client_command_id")
                val taskId = result.optJSONObject("response")
                    ?.optJSONObject("task")
                    ?.optString("id")
                    .orEmpty()
                if (clientCommandId.isBlank() || taskId.isBlank()) {
                    continue
                }
                writableDatabase.update(
                    "task_photo_outbox",
                    ContentValues().apply { put("server_task_id", taskId) },
                    "target_client_command_id = ? AND server_task_id IS NULL",
                    arrayOf(clientCommandId),
                )
            }
            writableDatabase.setTransactionSuccessful()
        } finally {
            writableDatabase.endTransaction()
        }
    }

    fun enqueueTaskPhoto(
        farmId: String,
        serverTaskId: String?,
        targetClientCommandId: String?,
        filePath: String,
        originalFilename: String,
        contentType: String,
        byteSize: Long,
        caption: String?,
        capturedAt: String?,
    ): String {
        val clientAttachmentId = java.util.UUID.randomUUID().toString()
        writableDatabase.insert(
            "task_photo_outbox",
            null,
            ContentValues().apply {
                put("client_attachment_id", clientAttachmentId)
                put("farm_id", farmId)
                put("server_task_id", serverTaskId)
                put("target_client_command_id", targetClientCommandId)
                put("file_path", filePath)
                put("original_filename", originalFilename)
                put("content_type", contentType)
                put("byte_size", byteSize)
                put("caption", caption)
                put("captured_at", capturedAt)
                put("status", "pending")
                put("created_at", System.currentTimeMillis())
                put("retry_count", 0)
            },
        )
        return clientAttachmentId
    }

    fun pendingTaskPhotos(limit: Int = 20): List<TaskPhotoOutboxItem> {
        val photos = mutableListOf<TaskPhotoOutboxItem>()
        readableDatabase.rawQuery(
            """
            SELECT client_attachment_id, farm_id, server_task_id, target_client_command_id,
                   file_path, original_filename, content_type, byte_size, caption, captured_at,
                   retry_count, last_error
            FROM task_photo_outbox
            WHERE status IN ('pending', 'failed') AND server_task_id IS NOT NULL
            ORDER BY created_at ASC
            LIMIT ?
            """.trimIndent(),
            arrayOf(limit.toString()),
        ).use { cursor ->
            while (cursor.moveToNext()) {
                photos.add(
                    TaskPhotoOutboxItem(
                        clientAttachmentId = cursor.getString(0),
                        farmId = cursor.getString(1),
                        serverTaskId = cursor.getString(2),
                        targetClientCommandId = cursor.getString(3),
                        filePath = cursor.getString(4),
                        originalFilename = cursor.getString(5),
                        contentType = cursor.getString(6),
                        byteSize = cursor.getLong(7),
                        caption = cursor.getString(8),
                        capturedAt = cursor.getString(9),
                        retryCount = cursor.getInt(10),
                        lastError = cursor.getString(11),
                    )
                )
            }
        }
        return photos
    }

    fun markTaskPhotoUploaded(clientAttachmentId: String) {
        writableDatabase.delete(
            "task_photo_outbox",
            "client_attachment_id = ?",
            arrayOf(clientAttachmentId),
        )
    }

    fun markTaskPhotoFailed(clientAttachmentId: String, message: String) {
        writableDatabase.update(
            "task_photo_outbox",
            ContentValues().apply {
                put("status", "failed")
                put("last_error", message)
                put("retry_count", taskPhotoRetryCount(clientAttachmentId) + 1)
            },
            "client_attachment_id = ?",
            arrayOf(clientAttachmentId),
        )
    }

    fun pendingTaskPhotoCount(): Int =
        readableDatabase.rawQuery(
            "SELECT COUNT(*) FROM task_photo_outbox WHERE status IN ('pending', 'failed')",
            emptyArray(),
        ).use { cursor ->
            if (cursor.moveToFirst()) cursor.getInt(0) else 0
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

    private fun taskPhotoRetryCount(clientAttachmentId: String): Int =
        readableDatabase.rawQuery(
            "SELECT retry_count FROM task_photo_outbox WHERE client_attachment_id = ?",
            arrayOf(clientAttachmentId),
        ).use { cursor ->
            if (cursor.moveToFirst()) cursor.getInt(0) else 0
        }

    private fun createTaskPhotoOutbox(db: SQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS task_photo_outbox (
                client_attachment_id TEXT PRIMARY KEY,
                farm_id TEXT NOT NULL,
                server_task_id TEXT,
                target_client_command_id TEXT,
                file_path TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                content_type TEXT NOT NULL,
                byte_size INTEGER NOT NULL,
                caption TEXT,
                captured_at TEXT,
                status TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                retry_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT
            )
            """.trimIndent()
        )
        db.execSQL("CREATE INDEX IF NOT EXISTS idx_task_photo_outbox_status ON task_photo_outbox(status)")
        db.execSQL(
            "CREATE INDEX IF NOT EXISTS idx_task_photo_outbox_target ON task_photo_outbox(target_client_command_id)"
        )
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

    private fun normalizeBaseUrl(value: String): String {
        return MobileBaseUrl.normalize(value)
    }

    private fun JSONObject.putNullable(key: String, value: String?): JSONObject =
        if (value == null) put(key, JSONObject.NULL) else put(key, value)

    private companion object {
        const val DB_NAME = "agritrack_field_store.db"
        const val DB_VERSION = 2
        const val DEFAULT_SYNC_INTERVAL_MINUTES = 5
    }
}

data class OutboxFailure(
    val clientCommandId: String,
    val type: String,
    val lastError: String?,
    val retryCount: Int,
)

data class TaskPhotoOutboxItem(
    val clientAttachmentId: String,
    val farmId: String,
    val serverTaskId: String,
    val targetClientCommandId: String?,
    val filePath: String,
    val originalFilename: String,
    val contentType: String,
    val byteSize: Long,
    val caption: String?,
    val capturedAt: String?,
    val retryCount: Int,
    val lastError: String?,
)
