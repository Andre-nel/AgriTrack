package com.agritrack.mobile.data;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.UUID;

public final class MobileCommand {
    public final String clientCommandId;
    public final String type;
    public final String farmId;
    public final JSONObject payload;

    public MobileCommand(String clientCommandId, String type, String farmId, JSONObject payload) {
        this.clientCommandId = clientCommandId;
        this.type = type;
        this.farmId = farmId;
        this.payload = payload;
    }

    public static MobileCommand fromJson(JSONObject json) throws JSONException {
        return new MobileCommand(
                json.getString("client_command_id"),
                json.getString("type"),
                json.getString("farm_id"),
                json.getJSONObject("payload")
        );
    }

    public static MobileCommand rainfall(String farmId, String recordedOn, double mm, String note)
            throws JSONException {
        JSONObject payload = new JSONObject()
                .put("recorded_on", recordedOn)
                .put("mm", mm)
                .put("note", note);
        return new MobileCommand(
                UUID.randomUUID().toString(),
                "rainfall.create",
                farmId,
                payload
        );
    }

    public JSONObject toJson() throws JSONException {
        return new JSONObject()
                .put("client_command_id", clientCommandId)
                .put("type", type)
                .put("farm_id", farmId)
                .put("payload", payload);
    }
}
