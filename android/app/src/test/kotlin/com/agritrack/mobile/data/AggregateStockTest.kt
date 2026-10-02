package com.agritrack.mobile.data

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class AggregateStockTest {
    @Test
    fun groupedReadModelKeepsCohortsInCacheButCountsEachTypeOnce() {
        val source = snapshot().mobs.first()
        assertEquals(2, source.balances.size)
        assertEquals(1, source.animalGroups.size)
        assertEquals(8, source.animalGroups.single().headCount)
        assertEquals("Sheep Merino ewe adult", source.animalGroups.single().animalGroupType.label)
        assertEquals("pregnant", source.balances.first().reproductiveState)
        assertEquals(source.totalLsu, source.animalGroups.single().headCount * source.animalGroups.single().animalGroupType.lsuPerHead, 0.000001)
    }

    @Test
    fun reductionUsesProportionsAndStableBalanceIdTieBreak() {
        val reduced = snapshot().withOptimisticCommands(JSONArray().put(count(4)))
        // 5/8 and 3/8 both have half-animal remainders; balance-a wins the extra removal.
        assertEquals(mapOf("balance-a" to 2, "balance-b" to 2), reduced.mobs.first().balances.associate { it.id to it.headCount })
        assertEquals(4, reduced.mobs.first().animalGroups.single().headCount)
        assertEquals(4.0, paddockGrazing(reduced, "paddock-1")!!.totalHead, 0.0)
    }

    @Test
    fun increasesAlwaysCreateFreshUnrecordedCohorts() {
        val first = count(10)
        val second = count(12)
        val increased = snapshot().withOptimisticCommands(JSONArray().put(first).put(second)).mobs.first()
        assertEquals(12, increased.totalHead)
        assertEquals(4, increased.balances.size)
        assertEquals(5, increased.balances.first { it.id == "balance-a" }.headCount)
        val arrivals = increased.balances.filter { it.id.startsWith("pending-") }
        assertEquals(listOf(2, 2), arrivals.map { it.headCount })
        assertEquals(2, arrivals.map { it.cohortId }.distinct().size)
        assertTrue(arrivals.all { it.reproductiveState == "not_recorded" && it.lactationState == "not_recorded" })
    }

    @Test
    fun noChangeAndZeroCountsHaveNoPhantomStock() {
        val unchanged = snapshot()
        assertEquals(unchanged.rawJson, unchanged.withOptimisticCommands(JSONArray().put(count(8))).rawJson)
        val empty = unchanged.withOptimisticCommands(JSONArray().put(count(0)))
        assertEquals(0, empty.mobs.first().totalHead)
        assertTrue(empty.mobs.first().animalGroups.isEmpty())
        assertEquals(0.0, paddockGrazing(empty, "paddock-1")!!.totalHead, 0.0)
    }

    @Test
    fun partialTransferConservesTotalsAndPreservesBothCohortStates() {
        val moved = snapshot().withOptimisticCommands(JSONArray().put(transfer(4)))
        val source = moved.mobs.first()
        val destination = moved.mobs.last()
        assertEquals(4, source.totalHead)
        assertEquals(6, destination.totalHead)
        assertEquals(1, destination.animalGroups.size)
        assertEquals(10, moved.mobs.sumOf { it.totalHead })
        assertEquals(3, destination.balances.first { it.reproductiveState == "pregnant" }.headCount)
        assertEquals(1, destination.balances.first { it.reproductiveState == "not_pregnant" }.headCount)
        assertEquals(3, destination.balances.map { it.id }.distinct().size)
        assertTrue(moved.mobs.flatMap { it.balances }.all { it.headCount >= 0 })
    }

    @Test
    fun fullTransferKeepsExistingCohortIdentity() {
        val moved = snapshot().withOptimisticCommands(JSONArray().put(transfer(8)))
        assertEquals(0, moved.mobs.first().totalHead)
        assertEquals(10, moved.mobs.last().totalHead)
        assertEquals(setOf("cohort-a", "cohort-b", "cohort-destination"), moved.mobs.last().balances.map { it.cohortId }.toSet())
    }

    @Test
    fun invalidTransferDoesNotPartiallyChangeEitherMob() {
        val base = snapshot()
        val batch = transfer(3)
        batch.getJSONObject("payload").getJSONArray("transfers").put(
            JSONObject().put("animal_group_type_id", "group-1").put("quantity", 99),
        )
        assertEquals(base.rawJson, base.withOptimisticCommands(JSONArray().put(batch)).rawJson)
        assertEquals(base.rawJson, base.withOptimisticCommands(JSONArray().put(transfer(9))).rawJson)
    }

    @Test
    fun legacyExplicitCohortCountAndTransferStillTargetOnlyThatCohort() {
        val oldCount = MobileCommand.stockCount("farm-1", "mob-1", "group-1", 4, "", "cohort-a").toJson()
        val oldTransfer = MobileCommand.mobTransfer("farm-1", "mob-1", "mob-2", "group-1", "cohort-a", 2, "").toJson()
        val moved = snapshot().withOptimisticCommands(JSONArray().put(oldCount).put(oldTransfer))
        assertEquals(2, moved.mobs.first().balances.first { it.cohortId == "cohort-a" }.headCount)
        assertEquals(3, moved.mobs.first().balances.first { it.cohortId == "cohort-b" }.headCount)
        assertEquals(4, moved.mobs.last().totalHead)
        assertFalse(AggregateStockPolicy.requiresSupport(JSONArray().put(oldCount).put(oldTransfer)))
    }

    @Test
    fun countThenTransferThenCountAllocationUsesWholeAnimalTypeOffline() {
        val move = MobileCommand.mobMoveCountAllocations(
            "farm-1", "mob-1", listOf(MobMoveCountAllocation("paddock-1", listOf(MobMoveGroupCount("group-1", 6)))),
        ).toJson()
        val projected = snapshot().withOptimisticCommands(JSONArray().put(count(10)).put(transfer(4)).put(move))
        assertEquals(6, projected.mobs.first().animalGroups.single().headCount)
        assertEquals(6, projected.mobs.last().animalGroups.single().headCount)
        assertEquals(6.0, paddockGrazing(projected, "paddock-1")!!.totalHead, 0.0)
        assertEquals(1.0, projected.activeGrazing.first().allocations.single().allocationFraction, 0.000001)
    }

    @Test
    fun pendingNewAnimalTypeCanBeTransferredByTaxonomyWithoutSendingItsLocalId() {
        val add = MobileCommand.stockCount("farm-1", "mob-1", "Goat", "Boer", "ewe", "adult", 7, "").toJson()
        val withNewType = snapshot().withOptimisticCommands(JSONArray().put(add))
        val newType = withNewType.mobs.first().animalGroups.first { it.animalGroupType.species == "Goat" }
        val transfer = MobileCommand.mobTransfer(
            "farm-1", "mob-1", "mob-2", newType.animalGroupTypeId, null, 3, "", newType.animalGroupType,
        ).toJson()
        val wireRow = transfer.getJSONObject("payload").getJSONArray("transfers").getJSONObject(0)
        assertFalse(wireRow.has("animal_group_type_id"))
        assertFalse(wireRow.has("cohort_id"))
        assertEquals("Boer", wireRow.getJSONObject("animal_group_type").getString("breed"))
        val move = MobileCommand.mobMoveCountAllocations(
            "farm-1", "mob-1", listOf(MobMoveCountAllocation("paddock-1", listOf(
                MobMoveGroupCount("group-1", 8),
                MobMoveGroupCount(newType.animalGroupTypeId, 4, newType.animalGroupType),
            ))),
        ).toJson()
        val moveRow = move.getJSONObject("payload").getJSONArray("allocations").getJSONObject(0).getJSONArray("group_counts").getJSONObject(1)
        assertFalse(moveRow.has("animal_group_type_id"))
        assertEquals("Boer", moveRow.getJSONObject("animal_group_type").getString("breed"))
        val projected = snapshot().withOptimisticCommands(JSONArray().put(add).put(transfer).put(move))
        assertEquals(4, projected.mobs.first().animalGroups.first { it.animalGroupType.species == "Goat" }.headCount)
        assertEquals(3, projected.mobs.last().animalGroups.first { it.animalGroupType.species == "Goat" }.headCount)
        assertEquals(12.0, paddockGrazing(projected, "paddock-1")!!.totalHead, 0.0)
        assertEquals(1.0, projected.activeGrazing.first().allocations.single().allocationFraction, 0.000001)
    }

    @Test
    fun taxonomyRecountFindsExistingPendingTypeAndCanReduceItToZero() {
        val add = MobileCommand.stockCount("farm-1", "mob-1", "Goat", "Boer", "ewe", "adult", 7, "").toJson()
        val recount = MobileCommand.stockCount("farm-1", "mob-1", "Goat", "Boer", "ewe", "adult", 0, "").toJson()
        val projected = snapshot().withOptimisticCommands(JSONArray().put(add).put(recount))
        assertEquals(8, projected.mobs.first().totalHead)
        assertTrue(projected.mobs.first().animalGroups.none { it.animalGroupType.species == "Goat" })
    }

    @Test
    fun taxonomyMatchesServerBreedCaseSensitivityAndWhitespaceNormalization() {
        val differentBreed = MobileCommand.stockCount("farm-1", "mob-1", "Sheep", "merino", "ewe", "adult", 3, "").toJson()
        val distinct = snapshot().withOptimisticCommands(JSONArray().put(differentBreed)).mobs.first()
        assertEquals(11, distinct.totalHead)
        assertEquals(2, distinct.animalGroups.size)
        assertEquals(setOf("Merino", "merino"), distinct.animalGroups.map { it.animalGroupType.breed }.toSet())

        val sameBreed = MobileCommand.stockCount("farm-1", "mob-1", " SHEEP ", "  Merino \t", " EWE ", " ADULT ", 3, "").toJson()
        val normalized = snapshot().withOptimisticCommands(JSONArray().put(sameBreed)).mobs.first()
        assertEquals(3, normalized.totalHead)
        assertEquals(1, normalized.animalGroups.size)

        val whitespace = MobileCommand.stockCount("farm-1", "mob-1", "Goat", "Boer   Cross", "ewe", "adult", 2, "").toJson()
        val recounted = MobileCommand.stockCount("farm-1", "mob-1", "Goat", "Boer Cross", "ewe", "adult", 3, "").toJson()
        val combined = snapshot().withOptimisticCommands(JSONArray().put(whitespace).put(recounted)).mobs.first()
        assertEquals(11, combined.totalHead)
        assertEquals(3, combined.animalGroups.first { it.animalGroupType.breed == "Boer Cross" }.headCount)
    }

    private fun count(quantity: Int): JSONObject =
        MobileCommand.stockCount("farm-1", "mob-1", "group-1", quantity, "").toJson()

    private fun transfer(quantity: Int): JSONObject =
        MobileCommand.mobTransfer("farm-1", "mob-1", "mob-2", "group-1", null, quantity, "").toJson()

    private fun snapshot(): FarmSnapshot = FarmSnapshot.fromJson(
        JSONObject()
            .put("farm", JSONObject().put("id", "farm-1").put("name", "Farm"))
            .put("paddocks", JSONArray().put(JSONObject().put("id", "paddock-1").put("name", "Camp").put("area_ha", 10)))
            .put("mobs", JSONArray()
                .put(mob("mob-1", JSONArray().put(balance("balance-a", "cohort-a", 5, "pregnant")).put(balance("balance-b", "cohort-b", 3, "not_pregnant"))))
                .put(mob("mob-2", JSONArray().put(balance("balance-destination", "cohort-destination", 2, "not_recorded")))))
            .put("active_grazing", JSONArray().put(JSONObject()
                .put("id", "grazing-1").put("mob_id", "mob-1")
                .put("allocations", JSONArray().put(JSONObject().put("paddock_id", "paddock-1")
                    .put("group_counts", JSONArray().put(JSONObject().put("animal_group_type_id", "group-1").put("head_count", 8).put("group_fraction", 1.0))))))),
    )

    private fun mob(id: String, balances: JSONArray): JSONObject =
        JSONObject().put("id", id).put("name", id).put("status", "active").put("balances", balances)

    private fun balance(id: String, cohortId: String, count: Int, state: String): JSONObject =
        JSONObject().put("id", id).put("cohort_id", cohortId).put("animal_group_type_id", "group-1")
            .put("head_count", count).put("reproductive_state", state)
            .put("animal_group_type", JSONObject().put("id", "group-1").put("species", "Sheep").put("breed", "Merino").put("sex", "ewe").put("age_class", "adult"))
}
