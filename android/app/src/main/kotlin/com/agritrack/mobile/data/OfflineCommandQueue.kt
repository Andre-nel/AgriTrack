package com.agritrack.mobile.data

import android.content.Context
import android.content.SharedPreferences
import org.json.JSONArray

class OfflineCommandQueue(
    private val store: CommandStore,
) {
    interface CommandStore {
        fun read(): String?
        fun write(value: String)
    }

    class SharedPreferencesCommandStore(context: Context) : CommandStore {
        private val prefs: SharedPreferences = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

        override fun read(): String? = prefs.getString(KEY_COMMANDS, "[]")

        override fun write(value: String) {
            prefs.edit().putString(KEY_COMMANDS, value).apply()
        }

        private companion object {
            const val PREFS = "agritrack_mobile_queue"
            const val KEY_COMMANDS = "commands"
        }
    }

    fun enqueue(command: MobileCommand) {
        val commands = readArray()
        commands.put(command.toJson())
        store.write(commands.toString())
    }

    fun pending(): List<MobileCommand> {
        val commands = readArray()
        return buildList {
            for (index in 0 until commands.length()) {
                add(MobileCommand.fromJson(commands.getJSONObject(index)))
            }
        }
    }

    fun pendingJson(): JSONArray = readArray()

    fun size(): Int = runCatching { readArray().length() }.getOrDefault(0)

    fun removeApplied(results: JSONArray) {
        val appliedIds = buildSet {
            for (index in 0 until results.length()) {
                val result = results.getJSONObject(index)
                if (result.optString("status") == "applied") {
                    add(result.optString("client_command_id"))
                }
            }
        }

        val commands = readArray()
        val retained = JSONArray()
        for (index in 0 until commands.length()) {
            val command = commands.getJSONObject(index)
            if (command.optString("client_command_id") !in appliedIds) {
                retained.put(command)
            }
        }
        store.write(retained.toString())
    }

    private fun readArray(): JSONArray {
        val raw = store.read()
        return if (raw.isNullOrBlank()) JSONArray() else JSONArray(raw)
    }
}
