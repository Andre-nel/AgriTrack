package com.agritrack.mobile.data;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;

import static org.junit.Assert.assertEquals;

public final class OfflineCommandQueueTest {
    @Test
    public void enqueueAndRemoveAppliedCommands() throws Exception {
        MemoryStore store = new MemoryStore();
        OfflineCommandQueue queue = new OfflineCommandQueue(store);
        MobileCommand first = MobileCommand.rainfall("farm-1", "2026-05-18", 2.5, "first");
        MobileCommand second = MobileCommand.rainfall("farm-1", "2026-05-19", 3.0, "second");

        queue.enqueue(first);
        queue.enqueue(second);

        assertEquals(2, queue.size());

        JSONArray results = new JSONArray()
                .put(new JSONObject()
                        .put("client_command_id", first.clientCommandId)
                        .put("status", "applied"))
                .put(new JSONObject()
                        .put("client_command_id", second.clientCommandId)
                        .put("status", "failed"));
        queue.removeApplied(results);

        assertEquals(1, queue.size());
        assertEquals(second.clientCommandId, queue.pending().get(0).clientCommandId);
    }

    private static final class MemoryStore implements OfflineCommandQueue.CommandStore {
        private String value = "[]";

        @Override
        public String read() {
            return value;
        }

        @Override
        public void write(String value) {
            this.value = value;
        }
    }
}
