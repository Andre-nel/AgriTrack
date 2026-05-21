package com.agritrack.mobile.data

class MobileRepository(
    private val apiClient: MobileApiClient,
    private val tokenStore: SecureTokenStore,
    private val snapshotCache: SnapshotCache,
    private val queue: OfflineCommandQueue,
) {
    fun loginAndLoad(email: String, password: String, deviceName: String): LoginLoadResult {
        val login = apiClient.login(email.trim(), password, deviceName)
        val token = login.getString("token")
        tokenStore.save(token)

        val bootstrap = BootstrapResult.fromJson(apiClient.bootstrap(token))
        val firstFarm = bootstrap.farms.firstOrNull()
        val snapshot = if (firstFarm == null) {
            null
        } else {
            FarmSnapshot.fromJson(apiClient.farmSnapshot(token, firstFarm.id)).also(snapshotCache::save)
        }

        return LoginLoadResult(bootstrap = bootstrap, snapshot = snapshot)
    }

    fun queueRainfall(farmId: String, recordedOn: String, mm: Double, note: String) {
        queue.enqueue(MobileCommand.rainfall(farmId, recordedOn, mm, note))
    }

    fun queueMobNote(farmId: String, mobId: String, description: String, tags: List<String>) {
        queue.enqueue(MobileCommand.mobNote(farmId, mobId, description, tags))
    }

    fun queueMobMove(farmId: String, mobId: String, paddockId: String, note: String) {
        queue.enqueue(MobileCommand.mobMove(farmId, mobId, paddockId))
        if (note.isNotBlank()) {
            queue.enqueue(
                MobileCommand.mobNote(
                    farmId = farmId,
                    mobId = mobId,
                    description = note,
                    tags = listOf("move"),
                )
            )
        }
    }

    fun queueStockCount(
        farmId: String,
        mobId: String,
        animalGroupTypeId: String,
        quantity: Int,
        note: String,
    ) {
        queue.enqueue(
            MobileCommand.stockCount(
                farmId = farmId,
                mobId = mobId,
                animalGroupTypeId = animalGroupTypeId,
                quantity = quantity,
                note = note,
            )
        )
    }

    fun syncQueuedCommands(): SyncSummary {
        val token = tokenStore.load() ?: error("No stored token. Log in first.")
        val response = apiClient.syncCommands(token, queue.pendingJson())
        val resultsJson = response.getJSONArray("results")
        queue.removeApplied(resultsJson)
        val results = buildList {
            for (index in 0 until resultsJson.length()) {
                add(SyncResult.fromJson(resultsJson.getJSONObject(index)))
            }
        }
        return SyncSummary(results = results, remainingQueueCount = queue.size())
    }
}
