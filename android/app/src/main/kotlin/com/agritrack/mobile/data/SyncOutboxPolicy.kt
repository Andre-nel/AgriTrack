package com.agritrack.mobile.data

import org.json.JSONObject

internal object SyncOutboxPolicy {
    private val terminalErrorCodes = setOf("invalid_command", "unsupported_command")

    fun shouldDrop(result: JSONObject): Boolean {
        if (result.optString("status") == "applied") {
            return true
        }
        if (result.optString("status") != "failed") {
            return false
        }
        val code = result.optJSONObject("error")?.optString("code").orEmpty()
        return code in terminalErrorCodes
    }
}
