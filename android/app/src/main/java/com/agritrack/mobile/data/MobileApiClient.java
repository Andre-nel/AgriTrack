package com.agritrack.mobile.data;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

public final class MobileApiClient {
    private final String baseUrl;

    public MobileApiClient(String baseUrl) {
        this.baseUrl = trimTrailingSlash(baseUrl);
    }

    public JSONObject login(String email, String password, String deviceName)
            throws IOException, JSONException {
        JSONObject body = new JSONObject()
                .put("email", email)
                .put("password", password)
                .put("device_name", deviceName);
        return request("POST", "/api/mobile/v1/auth/login", null, body);
    }

    public JSONObject bootstrap(String token) throws IOException, JSONException {
        return request("GET", "/api/mobile/v1/bootstrap", token, null);
    }

    public JSONObject farmSnapshot(String token, String farmId) throws IOException, JSONException {
        return request("GET", "/api/mobile/v1/farms/" + farmId + "/snapshot", token, null);
    }

    public JSONObject syncCommands(String token, JSONArray commands) throws IOException, JSONException {
        JSONObject body = new JSONObject().put("commands", commands);
        return request("POST", "/api/mobile/v1/sync/commands", token, body);
    }

    private JSONObject request(String method, String path, String token, JSONObject body)
            throws IOException, JSONException {
        HttpURLConnection connection = (HttpURLConnection) new URL(baseUrl + path).openConnection();
        connection.setRequestMethod(method);
        connection.setConnectTimeout(10000);
        connection.setReadTimeout(15000);
        connection.setRequestProperty("Accept", "application/json");
        if (token != null && !token.isEmpty()) {
            connection.setRequestProperty("Authorization", "Bearer " + token);
        }
        if (body != null) {
            byte[] bytes = body.toString().getBytes(StandardCharsets.UTF_8);
            connection.setDoOutput(true);
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            connection.setRequestProperty("Content-Length", String.valueOf(bytes.length));
            try (OutputStream output = connection.getOutputStream()) {
                output.write(bytes);
            }
        }

        int status = connection.getResponseCode();
        String responseBody = readBody(status >= 400 ? connection.getErrorStream() : connection.getInputStream());
        JSONObject response = responseBody.isEmpty() ? new JSONObject() : new JSONObject(responseBody);
        if (status >= 400) {
            String message = response.optJSONObject("error") != null
                    ? response.getJSONObject("error").optString("message")
                    : "HTTP " + status;
            throw new IOException(message);
        }
        return response;
    }

    private static String readBody(InputStream stream) throws IOException {
        if (stream == null) {
            return "";
        }
        StringBuilder builder = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) {
                builder.append(line);
            }
        }
        return builder.toString();
    }

    private static String trimTrailingSlash(String value) {
        String trimmed = value == null ? "" : value.trim();
        while (trimmed.endsWith("/")) {
            trimmed = trimmed.substring(0, trimmed.length() - 1);
        }
        return trimmed;
    }
}
