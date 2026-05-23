package com.agritrack.mobile.data

import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.IOException
import java.io.InputStream
import java.io.InputStreamReader
import java.io.OutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.nio.charset.StandardCharsets

class MobileApiClient(baseUrl: String) {
    private val baseUrl: String = trimTrailingSlash(baseUrl)

    fun login(email: String, password: String, deviceName: String): JSONObject {
        val body = JSONObject()
            .put("email", email)
            .put("password", password)
            .put("device_name", deviceName)
        return request("POST", "/api/mobile/v1/auth/login", null, body)
    }

    fun logout(token: String): JSONObject = request("POST", "/api/mobile/v1/auth/logout", token, null)

    fun bootstrap(token: String): JSONObject = request("GET", "/api/mobile/v1/bootstrap", token, null)

    fun farmSnapshot(token: String, farmId: String): JSONObject =
        request("GET", "/api/mobile/v1/farms/$farmId/snapshot", token, null)

    fun syncCommands(token: String, commands: JSONArray): JSONObject {
        val body = JSONObject().put("commands", commands)
        return request("POST", "/api/mobile/v1/sync/commands", token, body)
    }

    private fun request(method: String, path: String, token: String?, body: JSONObject?): JSONObject {
        if (baseUrl.isBlank()) {
            throw IOException("Base URL is required")
        }

        val connection = URL(baseUrl + path).openConnection() as HttpURLConnection
        try {
            connection.requestMethod = method
            connection.connectTimeout = 10_000
            connection.readTimeout = 15_000
            connection.setRequestProperty("Accept", "application/json")
            if (!token.isNullOrBlank()) {
                connection.setRequestProperty("Authorization", "Bearer $token")
            }
            if (body != null) {
                val bytes = body.toString().toByteArray(StandardCharsets.UTF_8)
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                connection.setRequestProperty("Content-Length", bytes.size.toString())
                connection.outputStream.use { output: OutputStream ->
                    output.write(bytes)
                }
            }

            val status = connection.responseCode
            val responseBody = readBody(if (status >= 400) connection.errorStream else connection.inputStream)
            val response = if (responseBody.isBlank()) JSONObject() else JSONObject(responseBody)
            if (status >= 400) {
                val message = response.optJSONObject("error")?.optString("message")
                    ?: "HTTP $status"
                throw IOException(message)
            }
            return response
        } finally {
            connection.disconnect()
        }
    }

    private fun readBody(stream: InputStream?): String {
        if (stream == null) {
            return ""
        }
        val builder = StringBuilder()
        BufferedReader(InputStreamReader(stream, StandardCharsets.UTF_8)).use { reader ->
            var line = reader.readLine()
            while (line != null) {
                builder.append(line)
                line = reader.readLine()
            }
        }
        return builder.toString()
    }

    private fun trimTrailingSlash(value: String?): String {
        var trimmed = value.orEmpty().trim()
        while (trimmed.endsWith("/")) {
            trimmed = trimmed.dropLast(1)
        }
        return trimmed
    }
}
