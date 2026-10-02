package com.agritrack.mobile.data

import org.json.JSONArray
import org.json.JSONObject

/** Keep unsupported stock operations out of the server's terminal-error path. */
internal object AggregateStockPolicy {
    const val unavailableMessage = "Stock sync paused. Connect to an updated server that supports animal group counts and transfers. Queued changes have been kept."

    fun cacheKey(baseUrl: String): String =
        "aggregate_stock_operations:${MobileBaseUrl.normalize(baseUrl)}"

    fun requiresSupport(commands: JSONArray): Boolean =
        (0 until commands.length()).any { index ->
            val command = commands.optJSONObject(index) ?: return@any false
            val payload = command.optJSONObject("payload") ?: JSONObject()
            when (command.optString("type")) {
                "stock_count.record" -> !payload.hasCohortSelector()
                "mob.transfer" -> {
                    val transfers = payload.optJSONArray("transfers") ?: JSONArray()
                    (0 until transfers.length()).any { row ->
                        transfers.optJSONObject(row)?.let { !it.hasCohortSelector() } ?: false
                    }
                }
                "mob.move" -> {
                    val allocations = payload.optJSONArray("allocations") ?: JSONArray()
                    (0 until allocations.length()).any { allocationIndex ->
                        val counts = allocations.optJSONObject(allocationIndex)?.optJSONArray("group_counts") ?: JSONArray()
                        (0 until counts.length()).any { countIndex ->
                            counts.optJSONObject(countIndex)?.optJSONObject("animal_group_type") != null
                        }
                    }
                }
                else -> false
            }
        }

    fun confirmBatch(
        commands: JSONArray,
        fetchBootstrap: () -> BootstrapResult,
        saveSupport: (Boolean) -> Unit,
    ): AggregateStockSyncCheck {
        if (!requiresSupport(commands)) return AggregateStockSyncCheck()
        val supported = try {
            fetchBootstrap().aggregateStockOperations
        } catch (exc: Exception) {
            return AggregateStockSyncCheck(
                pauseMessage = "Stock sync paused: could not confirm server support. Queued changes have been kept. ${exc.message.orEmpty()}".trim(),
            )
        }
        saveSupport(supported)
        return AggregateStockSyncCheck(
            supported = supported,
            pauseMessage = if (supported) null else unavailableMessage,
        )
    }

    private fun JSONObject.hasCohortSelector(): Boolean =
        !isNull("cohort_id") && optString("cohort_id").isNotBlank()
}

internal data class AggregateStockSyncCheck(
    val supported: Boolean? = null,
    val pauseMessage: String? = null,
)
