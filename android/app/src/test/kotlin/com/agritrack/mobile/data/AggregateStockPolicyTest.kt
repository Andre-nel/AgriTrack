package com.agritrack.mobile.data

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class AggregateStockPolicyTest {
    @Test
    fun bootstrapCapabilityDefaultsToFalseAndRequiresExplicitSupport() {
        assertFalse(BootstrapResult.fromJson(JSONObject()).aggregateStockOperations)
        assertFalse(bootstrap(false).aggregateStockOperations)
        assertTrue(bootstrap(true).aggregateStockOperations)
    }

    @Test
    fun cacheKeyNormalizesServerUrlAndSeparatesServers() {
        assertEquals(AggregateStockPolicy.cacheKey(" http://farm.local:5000/ "), AggregateStockPolicy.cacheKey("http://farm.local:5000"))
        assertNotEquals(AggregateStockPolicy.cacheKey("http://farm.local:5000"), AggregateStockPolicy.cacheKey("http://other.local:5000"))
        assertNotEquals(AggregateStockPolicy.cacheKey("http://farm.local:5000"), AggregateStockPolicy.cacheKey("https://farm.local:5000"))
    }

    @Test
    fun falseAfterTrueConfirmationPausesWholeBatchWithoutMutatingCommands() {
        val commands = batch()
        val before = commands.toString()
        var cached = true
        val result = AggregateStockPolicy.confirmBatch(commands, { bootstrap(false) }, { cached = it })
        assertFalse(cached)
        assertFalse(result.supported!!)
        assertNotNull(result.pauseMessage)
        assertEquals(before, commands.toString())
        assertEquals(3, commands.length())
    }

    @Test
    fun confirmationFailureRetainsCachedOfflineSupportAndLeavesBatchUntouched() {
        val commands = batch()
        val before = commands.toString()
        var wroteCache = false
        val result = AggregateStockPolicy.confirmBatch(commands, { error("Offline") }, { wroteCache = true })
        assertFalse(wroteCache)
        assertNull(result.supported)
        assertNotNull(result.pauseMessage)
        assertEquals(before, commands.toString())
    }

    @Test
    fun cachedSupportNeverSubstitutesForFreshConfirmationAtSync() {
        var fetches = 0
        val cachedValues = mutableListOf<Boolean>()
        repeat(2) {
            val result = AggregateStockPolicy.confirmBatch(batch(), { fetches++; bootstrap(true) }, { cachedValues.add(it) })
            assertNull(result.pauseMessage)
            assertEquals(true, result.supported)
        }
        assertEquals(2, fetches)
        assertEquals(listOf(true, true), cachedValues)
    }

    @Test
    fun unrelatedAndExplicitLegacyCommandsDoNotRequireCapability() {
        val commands = JSONArray()
            .put(MobileCommand.rainfall("farm-1", "2026-09-22", 4.0, "").toJson())
            .put(MobileCommand.stockCount("farm-1", "mob-1", "group-1", 3, "", "cohort-a").toJson())
            .put(MobileCommand.mobTransfer("farm-1", "mob-1", "mob-2", "group-1", "cohort-a", 2, "").toJson())
        val result = AggregateStockPolicy.confirmBatch(commands, { error("Must not fetch bootstrap") }, { error("Must not save capability") })
        assertNull(result.pauseMessage)
        assertNull(result.supported)
    }

    @Test
    fun anyGroupOnlyTransferRowRequiresSupportIncludingNullSelector() {
        val transfer = MobileCommand.mobTransfer("farm-1", "mob-1", "mob-2", "group-1", "cohort-a", 2, "").toJson()
        transfer.getJSONObject("payload").getJSONArray("transfers").put(
            JSONObject().put("animal_group_type_id", "group-2").put("cohort_id", JSONObject.NULL).put("quantity", 1),
        )
        assertTrue(AggregateStockPolicy.requiresSupport(JSONArray().put(transfer)))
    }

    @Test
    fun metadataOnlyMoveRequiresSupportButExistingGroupMoveRemainsCompatible() {
        val group = AnimalGroupTypeSummary("pending-type", "Sheep", "Merino", "ewe", "adult")
        val pendingMove = MobileCommand.mobMoveCountAllocations("farm-1", "mob-1", listOf(
            MobMoveCountAllocation("paddock-1", listOf(MobMoveGroupCount(group.id, 4, group))),
        )).toJson()
        val ordinaryMove = MobileCommand.mobMoveCountAllocations("farm-1", "mob-1", listOf(
            MobMoveCountAllocation("paddock-1", listOf(MobMoveGroupCount("group-1", 4))),
        )).toJson()
        assertTrue(AggregateStockPolicy.requiresSupport(JSONArray().put(pendingMove)))
        assertFalse(AggregateStockPolicy.requiresSupport(JSONArray().put(ordinaryMove)))
        val check = AggregateStockPolicy.confirmBatch(JSONArray().put(pendingMove), { bootstrap(false) }, {})
        assertNotNull(check.pauseMessage)
    }

    private fun bootstrap(supported: Boolean): BootstrapResult = BootstrapResult.fromJson(
        JSONObject().put("sync", JSONObject().put("aggregate_stock_operations", supported)),
    )

    private fun batch(): JSONArray = JSONArray()
        .put(MobileCommand.rainfall("farm-1", "2026-09-22", 4.0, "").toJson())
        .put(MobileCommand.stockCount("farm-1", "mob-1", "group-1", 4, "").toJson())
        .put(MobileCommand.mobMove("farm-1", "mob-1", "paddock-1").toJson())
}
