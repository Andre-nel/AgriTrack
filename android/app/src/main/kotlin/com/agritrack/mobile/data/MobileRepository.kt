package com.agritrack.mobile.data

class MobileRepository(
    private val apiClient: MobileApiClient,
    private val tokenStore: SecureTokenStore,
    private val fieldStore: LocalFieldStore,
) {
    fun loadCachedSnapshot(): FarmSnapshot? = fieldStore.loadLastSnapshot()

    fun ping(): Boolean {
        val token = tokenStore.load() ?: return false
        apiClient.ping(token)
        return true
    }

    fun loginAndLoad(email: String, password: String, deviceName: String): LoginLoadResult {
        val login = apiClient.login(email.trim(), password, deviceName)
        val token = login.getString("token")
        tokenStore.save(token)

        try {
            val bootstrap = BootstrapResult.fromJson(apiClient.bootstrap(token))
            val firstFarm = bootstrap.farms.firstOrNull()
            val snapshot = if (firstFarm == null) {
                null
            } else {
                refreshSnapshot(firstFarm.id, token)
            }

            return LoginLoadResult(bootstrap = bootstrap, snapshot = snapshot)
        } catch (exc: Exception) {
            tokenStore.clear()
            throw exc
        }
    }

    fun logout(): LogoutResult {
        val token = runCatching { tokenStore.load() }.getOrElse { exc ->
            tokenStore.clear()
            return LogoutResult(serverRevoked = false, errorMessage = exc.message ?: "Stored login could not be read.")
        }

        val errorMessage = if (token.isNullOrBlank()) {
            null
        } else {
            runCatching { apiClient.logout(token) }.exceptionOrNull()?.message
        }
        tokenStore.clear()
        return LogoutResult(serverRevoked = errorMessage == null, errorMessage = errorMessage)
    }

    fun refreshSnapshot(farmId: String? = null): FarmSnapshot {
        val token = tokenStore.load() ?: error("No stored token. Log in first.")
        return refreshSnapshot(farmId ?: fieldStore.loadLastSnapshot()?.farm?.id.orEmpty(), token)
    }

    fun queueRainfall(farmId: String, recordedOn: String, mm: Double, note: String) {
        fieldStore.enqueue(MobileCommand.rainfall(farmId, recordedOn, mm, note))
    }

    fun queueMobNote(farmId: String, mobId: String, description: String, tags: List<String>) {
        fieldStore.enqueue(MobileCommand.mobNote(farmId, mobId, description, tags))
    }

    fun queueMobMove(farmId: String, mobId: String, paddockId: String, note: String) {
        fieldStore.enqueue(MobileCommand.mobMove(farmId, mobId, paddockId))
        if (note.isNotBlank()) {
            fieldStore.enqueue(
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
        fieldStore.enqueue(
            MobileCommand.stockCount(
                farmId = farmId,
                mobId = mobId,
                animalGroupTypeId = animalGroupTypeId,
                quantity = quantity,
                note = note,
            )
        )
    }

    fun queueWaterStatus(
        farmId: String,
        waterAssetId: String,
        status: String,
        waterLevel: String,
        active: Boolean,
    ) {
        fieldStore.enqueue(MobileCommand.waterAssetStatus(farmId, waterAssetId, status, waterLevel, active))
    }

    fun queuePaddockUpdate(
        farmId: String,
        paddockId: String,
        status: String,
        notes: String,
        tags: String,
    ) {
        fieldStore.enqueue(MobileCommand.paddockUpdate(farmId, paddockId, status, notes, tags))
    }

    fun queueTaskCreate(
        farmId: String,
        heading: String,
        description: String,
        dueDate: String,
        entityType: String,
        entityId: String,
    ): String {
        val command = MobileCommand.taskCreate(farmId, heading, description, dueDate, entityType, entityId)
        fieldStore.enqueue(command)
        return command.clientCommandId
    }

    fun queueTaskStatus(farmId: String, taskId: String, status: String, note: String) {
        fieldStore.enqueue(MobileCommand.taskStatus(farmId, taskId, status, note))
    }

    fun queueTaskComment(farmId: String, taskId: String, body: String) {
        fieldStore.enqueue(MobileCommand.taskComment(farmId, taskId, body))
    }

    fun queueMobTransfer(
        farmId: String,
        sourceMobId: String,
        destinationMobId: String,
        animalGroupTypeId: String,
        quantity: Int,
        note: String,
    ) {
        fieldStore.enqueue(
            MobileCommand.mobTransfer(
                farmId = farmId,
                sourceMobId = sourceMobId,
                destinationMobId = destinationMobId,
                animalGroupTypeId = animalGroupTypeId,
                quantity = quantity,
                note = note,
            )
        )
    }

    fun queueTaskPhoto(
        farmId: String,
        serverTaskId: String?,
        targetClientCommandId: String?,
        filePath: String,
        originalFilename: String,
        contentType: String,
        byteSize: Long,
        caption: String?,
        capturedAt: String?,
    ) {
        fieldStore.enqueueTaskPhoto(
            farmId = farmId,
            serverTaskId = serverTaskId,
            targetClientCommandId = targetClientCommandId,
            filePath = filePath,
            originalFilename = originalFilename,
            contentType = contentType,
            byteSize = byteSize,
            caption = caption,
            capturedAt = capturedAt,
        )
    }

    fun syncQueuedCommands(refreshAfterSync: Boolean = true): SyncSummary {
        val token = tokenStore.load() ?: error("No stored token. Log in first.")
        val pending = fieldStore.pendingJson()
        if (pending.length() == 0) {
            uploadPendingTaskPhotos(token)
            val snapshot = if (refreshAfterSync) {
                runCatching { refreshLastSnapshot(token) }.getOrNull()
            } else {
                null
            }
            return SyncSummary(
                results = emptyList(),
                remainingQueueCount = fieldStore.pendingCount(),
                refreshedSnapshot = snapshot,
            )
        }
        val response = apiClient.syncCommands(token, pending)
        val resultsJson = response.getJSONArray("results")
        fieldStore.applySyncResults(resultsJson)
        fieldStore.resolveTaskPhotoTargets(resultsJson)
        val results = buildList {
            for (index in 0 until resultsJson.length()) {
                add(SyncResult.fromJson(resultsJson.getJSONObject(index)))
            }
        }
        uploadPendingTaskPhotos(token)
        val refreshed = if (refreshAfterSync && results.any { it.status == "applied" }) {
            runCatching { refreshLastSnapshot(token) }.getOrNull()
        } else {
            null
        }
        return SyncSummary(
            results = results,
            remainingQueueCount = fieldStore.pendingCount(),
            refreshedSnapshot = refreshed,
        )
    }

    fun pendingCount(): Int = fieldStore.pendingCount()

    fun failedCommands(): List<OutboxFailure> = fieldStore.failedCommands()

    fun syncIntervalMinutes(): Int = fieldStore.getSyncIntervalMinutes()

    fun setSyncIntervalMinutes(value: Int) {
        fieldStore.setSyncIntervalMinutes(value)
    }

    private fun refreshSnapshot(farmId: String, token: String): FarmSnapshot {
        if (farmId.isBlank()) {
            error("No farm is available to refresh.")
        }
        val snapshot = FarmSnapshot.fromJson(apiClient.farmSnapshot(token, farmId))
        fieldStore.saveSnapshot(snapshot)
        return snapshot
    }

    private fun refreshLastSnapshot(token: String): FarmSnapshot {
        val farmId = fieldStore.loadLastSnapshot()?.farm?.id ?: error("No cached farm to refresh.")
        return refreshSnapshot(farmId, token)
    }

    private fun uploadPendingTaskPhotos(token: String) {
        fieldStore.pendingTaskPhotos().forEach { photo ->
            val error = runCatching { apiClient.uploadTaskAttachment(token, photo) }.exceptionOrNull()
            if (error == null) {
                fieldStore.markTaskPhotoUploaded(photo.clientAttachmentId)
            } else {
                fieldStore.markTaskPhotoFailed(photo.clientAttachmentId, error.message ?: "Photo upload failed")
            }
        }
    }
}

data class LogoutResult(
    val serverRevoked: Boolean,
    val errorMessage: String?,
)
