package com.agritrack.mobile.data

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
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

    fun ping(token: String): JSONObject = request("GET", "/api/mobile/v1/ping", token, null)

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

    fun uploadTaskAttachment(token: String, photo: TaskPhotoOutboxItem): JSONObject {
        val file = File(photo.filePath)
        if (!file.exists()) {
            throw IOException("Photo file is missing")
        }

        val boundary = "AgriTrackBoundary${System.currentTimeMillis()}"
        val connection = URL(
            "$baseUrl/api/mobile/v1/farms/${photo.farmId}/tasks/${photo.serverTaskId}/attachments"
        ).openConnection() as HttpURLConnection
        try {
            connection.requestMethod = "POST"
            connection.connectTimeout = 10_000
            connection.readTimeout = 30_000
            connection.doOutput = true
            connection.setRequestProperty("Accept", "application/json")
            connection.setRequestProperty("Authorization", "Bearer $token")
            connection.setRequestProperty("Content-Type", "multipart/form-data; boundary=$boundary")
            connection.outputStream.use { output ->
                writePart(output, boundary, "client_attachment_id", photo.clientAttachmentId)
                photo.caption?.takeIf { it.isNotBlank() }?.let {
                    writePart(output, boundary, "caption", it)
                }
                photo.capturedAt?.takeIf { it.isNotBlank() }?.let {
                    writePart(output, boundary, "captured_at", it)
                }
                writeFilePart(output, boundary, "file", file, photo.originalFilename, photo.contentType)
                output.write("--$boundary--\r\n".toByteArray(StandardCharsets.UTF_8))
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

    private fun writePart(output: OutputStream, boundary: String, name: String, value: String) {
        output.write("--$boundary\r\n".toByteArray(StandardCharsets.UTF_8))
        output.write("Content-Disposition: form-data; name=\"$name\"\r\n\r\n".toByteArray(StandardCharsets.UTF_8))
        output.write(value.toByteArray(StandardCharsets.UTF_8))
        output.write("\r\n".toByteArray(StandardCharsets.UTF_8))
    }

    private fun writeFilePart(
        output: OutputStream,
        boundary: String,
        name: String,
        file: File,
        filename: String,
        contentType: String,
    ) {
        output.write("--$boundary\r\n".toByteArray(StandardCharsets.UTF_8))
        output.write(
            (
                "Content-Disposition: form-data; name=\"$name\"; filename=\"$filename\"\r\n" +
                    "Content-Type: $contentType\r\n\r\n"
                ).toByteArray(StandardCharsets.UTF_8)
        )
        file.inputStream().use { input -> input.copyTo(output) }
        output.write("\r\n".toByteArray(StandardCharsets.UTF_8))
    }

    private fun trimTrailingSlash(value: String?): String {
        var trimmed = value.orEmpty().trim()
        while (trimmed.endsWith("/")) {
            trimmed = trimmed.dropLast(1)
        }
        return trimmed
    }
}
