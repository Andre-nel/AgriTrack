package com.agritrack.mobile

import com.agritrack.mobile.data.AnimalGroupTypeSummary
import com.agritrack.mobile.data.MobBalanceSummary
import com.agritrack.mobile.data.MobSummary
import org.junit.Assert.assertEquals
import org.junit.Test

class CountMoveAllocationNormalizationTest {
    @Test
    fun countModeMergesDuplicateDestinationPaddocks() {
        val mob = MobSummary(
            id = "mob-1",
            name = "Mixed Mob",
            status = "active",
            balances = listOf(
                MobBalanceSummary(
                    id = "balance-sheep",
                    animalGroupTypeId = "group-sheep",
                    animalGroupType = AnimalGroupTypeSummary(
                        id = "group-sheep",
                        species = "Sheep",
                        breed = "Merino",
                        sex = "ewe",
                        ageClass = "adult",
                    ),
                    headCount = 12,
                ),
                MobBalanceSummary(
                    id = "balance-cattle",
                    animalGroupTypeId = "group-cattle",
                    animalGroupType = AnimalGroupTypeSummary(
                        id = "group-cattle",
                        species = "Cattle",
                        breed = "Bonsmara",
                        sex = "cow",
                        ageClass = "adult",
                    ),
                    headCount = 5,
                ),
            ),
        )

        val allocations = normalizeCountMoveAllocations(
            drafts = listOf(
                CountMoveAllocationDraft(
                    paddockId = "north",
                    groupCounts = mapOf("group-sheep" to "5", "group-cattle" to "0"),
                ),
                CountMoveAllocationDraft(
                    paddockId = "north",
                    groupCounts = mapOf("group-sheep" to "7", "group-cattle" to "0"),
                ),
                CountMoveAllocationDraft(
                    paddockId = "south",
                    groupCounts = mapOf("group-sheep" to "0", "group-cattle" to "5"),
                ),
            ),
            mob = mob,
        ).getOrThrow()

        assertEquals(listOf("north", "south"), allocations.map { it.paddockId })
        assertEquals(
            listOf("group-sheep" to 12),
            allocations[0].groupCounts.map { it.animalGroupTypeId to it.headCount },
        )
        assertEquals(
            listOf("group-cattle" to 5),
            allocations[1].groupCounts.map { it.animalGroupTypeId to it.headCount },
        )
    }
}
