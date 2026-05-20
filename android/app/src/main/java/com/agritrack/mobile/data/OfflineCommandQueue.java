package com.agritrack.mobile.data;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

public final class OfflineCommandQueue {
    public interface CommandStore {
        String read();

        void write(String value);
    }

    public static final class SharedPreferencesCommandStore implements CommandStore {
        private static final String PREFS = "agritrack_mobile_queue";
        private static final String KEY_COMMANDS = "commands";

        private final SharedPreferences prefs;

        public SharedPreferencesCommandStore(Context context) {
            this.prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        }

        @Override
        public String read() {
            return prefs.getString(KEY_COMMANDS, "[]");
        }

        @Override
        public void write(String value) {
            prefs.edit().putString(KEY_COMMANDS, value).apply();
        }
    }

    private final CommandStore store;

    public OfflineCommandQueue(CommandStore store) {
        this.store = store;
    }

    public void enqueue(MobileCommand command) throws JSONException {
        JSONArray commands = readArray();
        commands.put(command.toJson());
        store.write(commands.toString());
    }

    public List<MobileCommand> pending() throws JSONException {
        JSONArray commands = readArray();
        List<MobileCommand> pending = new ArrayList<>();
        for (int index = 0; index < commands.length(); index++) {
            pending.add(MobileCommand.fromJson(commands.getJSONObject(index)));
        }
        return pending;
    }

    public JSONArray pendingJson() throws JSONException {
        return readArray();
    }

    public int size() {
        try {
            return readArray().length();
        } catch (JSONException exc) {
            return 0;
        }
    }

    public void removeApplied(JSONArray results) throws JSONException {
        Set<String> appliedIds = new HashSet<>();
        for (int index = 0; index < results.length(); index++) {
            JSONObject result = results.getJSONObject(index);
            if ("applied".equals(result.optString("status"))) {
                appliedIds.add(result.optString("client_command_id"));
            }
        }

        JSONArray commands = readArray();
        JSONArray retained = new JSONArray();
        for (int index = 0; index < commands.length(); index++) {
            JSONObject command = commands.getJSONObject(index);
            if (!appliedIds.contains(command.optString("client_command_id"))) {
                retained.put(command);
            }
        }
        store.write(retained.toString());
    }

    private JSONArray readArray() throws JSONException {
        String raw = store.read();
        if (raw == null || raw.trim().isEmpty()) {
            return new JSONArray();
        }
        return new JSONArray(raw);
    }
}
