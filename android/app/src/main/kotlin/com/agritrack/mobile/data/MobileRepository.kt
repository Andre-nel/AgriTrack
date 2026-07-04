package com.agritrack.mobile.data

import org.json.JSONArray
import java.util.UUID

class MobileRepository(
    private val apiClient: MobileApiClient,
    private val tokenStore: SecureTokenStore,
    private val fieldStore: LocalFieldStore,
) {
    fun loadCachedSnapshot(): FarmSnapshot? = fieldStore.loadLastSnapshot()

    fun loadCachedFarms(): List<FarmSummary> = fieldStore.loadAvailableFarms()

    fun ping(): Boolean {
        val token = tokenStore.load() ?: return false
        apiClient.ping(token)
        return true
    }

    fun loginAndLoad(email: String, password: String, deviceName: String): FarmLoadResult {
        val preferredFarmId = fieldStore.loadLastFarmId()
        val login = apiClient.login(email.trim(), password, deviceName)
        val token = login.getString("token")
        tokenStore.save(token)

        try {
            return loadFarmAccess(token, preferredFarmId)
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
        return refreshSnapshot(farmId ?: fieldStore.loadLastFarmId().orEmpty(), token, makeActive = true)
    }

    fun refreshAllSnapshots(): FarmLoadResult {
        val token = tokenStore.load() ?: error("No stored token. Log in first.")
        return loadFarmAccess(token, fieldStore.loadLastFarmId())
    }

    fun selectFarm(farmId: String): FarmSnapshot {
        val normalizedFarmId = farmId.trim()
        if (normalizedFarmId.isBlank()) {
            error("Choose a farm first.")
        }
        val cached = fieldStore.loadSnapshot(normalizedFarmId)
        val snapshot = cached ?: run {
            val token = tokenStore.load() ?: error("No stored token. Log in first.")
            refreshSnapshot(normalizedFarmId, token, makeActive = false)
        }
        fieldStore.setActiveFarmId(normalizedFarmId)
        return snapshot
    }

    fun queueRainfall(farmId: String, recordedOn: String, mm: Double, note: String) {
        fieldStore.enqueue(MobileCommand.rainfall(farmId, recordedOn, mm, note))
    }

    fun queueMobNote(farmId: String, mobId: String, description: String, tags: List<String>) {
        fieldStore.enqueue(MobileCommand.mobNote(farmId, mobId, description, tags))
    }

    fun queueMobCreate(farmId: String, name: String, originNote: String) {
        fieldStore.enqueue(MobileCommand.mobCreate(farmId, name, originNote))
    }

    fun queueMobMove(farmId: String, mobId: String, allocations: List<Pair<String, Double>>, note: String) {
        fieldStore.enqueue(MobileCommand.mobMoveAllocations(farmId, mobId, allocations))
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

    fun queueMobMoveCounts(
        farmId: String,
        mobId: String,
        allocations: List<MobMoveCountAllocation>,
        note: String,
    ) {
        fieldStore.enqueue(MobileCommand.mobMoveCountAllocations(farmId, mobId, allocations))
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

    fun queuePaddockNote(farmId: String, paddockId: String, description: String, tags: List<String>) {
        fieldStore.enqueue(MobileCommand.paddockNote(farmId, paddockId, description, tags))
    }

    fun queueWaterAssetNote(farmId: String, waterAssetId: String, description: String, tags: List<String>) {
        fieldStore.enqueue(MobileCommand.waterAssetNote(farmId, waterAssetId, description, tags))
    }

    fun queueFenceNote(
        farmId: String,
        fenceSectionId: String,
        description: String,
        tags: List<String>,
        eventType: String = "inspection",
        conditionAfter: String = "",
    ) {
        fieldStore.enqueue(
            MobileCommand.fenceNote(
                farmId = farmId,
                fenceSectionId = fenceSectionId,
                description = description,
                tags = tags,
                eventType = eventType,
                conditionAfter = conditionAfter,
            )
        )
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

    fun queueStockCountForNewGroup(
        farmId: String,
        mobId: String,
        species: String,
        breed: String,
        sex: String,
        ageClass: String,
        quantity: Int,
        note: String,
    ) {
        fieldStore.enqueue(
            MobileCommand.stockCount(
                farmId = farmId,
                mobId = mobId,
                species = species,
                breed = breed,
                sex = sex,
                ageClass = ageClass,
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

    fun queueFenceUpdate(
        farmId: String,
        fenceSectionId: String,
        condition: String,
        notes: String,
        electricWire: Boolean,
    ) {
        fieldStore.enqueue(MobileCommand.fenceUpdate(farmId, fenceSectionId, condition, notes, electricWire))
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

    fun queueGateUpdate(farmId: String, gateId: String, status: String) {
        fieldStore.enqueue(MobileCommand.gateUpdate(farmId, gateId, status))
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

    fun queueShearerCreate(farmId: String, name: String): String {
        val shearerId = UUID.randomUUID().toString()
        fieldStore.enqueue(MobileCommand.shearerCreate(farmId, shearerId, name))
        return shearerId
    }

    fun queueShearingSessionCreate(
        farmId: String,
        name: String,
        species: String,
        startDate: String,
        endDate: String,
        lootjieRate: Double,
        notes: String,
    ): String {
        val sessionId = UUID.randomUUID().toString()
        fieldStore.enqueue(
            MobileCommand.shearingSessionCreate(
                farmId = farmId,
                sessionId = sessionId,
                name = name,
                species = species,
                startDate = startDate,
                endDate = endDate,
                lootjieRate = lootjieRate,
                notes = notes,
            )
        )
        return sessionId
    }

    fun queueShearingSessionStatus(farmId: String, sessionId: String, status: String) {
        fieldStore.enqueue(MobileCommand.shearingSessionUpdate(farmId, sessionId, status))
    }

    fun queueShearingEntry(
        farmId: String,
        sessionId: String,
        workDate: String,
        shearerId: String,
        animalGroupType: AnimalGroupTypeSummary,
        quantity: Int,
        note: String,
    ): String {
        val entryId = UUID.randomUUID().toString()
        fieldStore.enqueue(
            MobileCommand.shearingEntryRecord(
                farmId = farmId,
                entryId = entryId,
                sessionId = sessionId,
                workDate = workDate,
                shearerId = shearerId,
                animalGroupType = animalGroupType,
                quantity = quantity,
                note = note,
            )
        )
        return entryId
    }

    fun queueShearingBaleCode(
        farmId: String,
        species: String,
        code: String,
        lineType: String,
        ageGroup: String,
        finenessGrade: String,
        lengthCode: String,
        finenessMicron: Double?,
        cleanYieldPercent: Double?,
        color: String,
        vegetableMatter: String,
        styleCharacter: String,
        consistency: String,
        fault: String,
        description: String,
        notes: String,
    ): String {
        val baleCodeId = UUID.randomUUID().toString()
        fieldStore.enqueue(
            MobileCommand.shearingBaleCodeUpsert(
                farmId = farmId,
                baleCodeId = baleCodeId,
                species = species,
                code = code,
                lineType = lineType,
                ageGroup = ageGroup,
                finenessGrade = finenessGrade,
                lengthCode = lengthCode,
                finenessMicron = finenessMicron,
                cleanYieldPercent = cleanYieldPercent,
                color = color,
                vegetableMatter = vegetableMatter,
                styleCharacter = styleCharacter,
                consistency = consistency,
                fault = fault,
                description = description,
                notes = notes,
            )
        )
        return baleCodeId
    }

    fun queueShearingBale(
        farmId: String,
        sessionId: String,
        baleCodeId: String?,
        codeText: String,
        baleNumber: String,
        weightKg: Double,
        pricePerKg: Double?,
        totalPrice: Double?,
        notes: String,
    ): String {
        val baleId = UUID.randomUUID().toString()
        fieldStore.enqueue(
            MobileCommand.shearingBaleRecord(
                farmId = farmId,
                baleId = baleId,
                sessionId = sessionId,
                baleCodeId = baleCodeId,
                codeText = codeText,
                baleNumber = baleNumber,
                weightKg = weightKg,
                pricePerKg = pricePerKg,
                totalPrice = totalPrice,
                notes = notes,
            )
        )
        return baleId
    }

    fun queueShearingBaleDelete(farmId: String, sessionId: String, baleId: String) {
        fieldStore.enqueue(MobileCommand.shearingBaleDelete(farmId, sessionId, baleId))
    }

    fun syncQueuedCommands(refreshAfterSync: Boolean = true): SyncSummary {
        val token = tokenStore.load() ?: error("No stored token. Log in first.")
        val pending = fieldStore.pendingJson()
        val activeFarmId = fieldStore.loadLastFarmId()
        val pendingFarmIdsByCommandId = commandFarmIds(pending)
        if (pending.length() == 0) {
            val photoFarmIds = uploadPendingTaskPhotos(token)
            val farmIdsToRefresh = if (!refreshAfterSync) {
                emptySet()
            } else if (photoFarmIds.isNotEmpty()) {
                photoFarmIds
            } else {
                activeFarmId?.let { setOf(it) }.orEmpty()
            }
            val refreshedSnapshots = refreshFarmIds(farmIdsToRefresh, token, activeFarmId)
            val refreshedSnapshot = if (activeFarmId == null) {
                null
            } else {
                refreshedSnapshots.firstOrNull { it.farm.id == activeFarmId }
            }
            return SyncSummary(
                results = emptyList(),
                remainingQueueCount = fieldStore.pendingCount(),
                refreshedSnapshot = refreshedSnapshot,
                refreshedSnapshots = refreshedSnapshots,
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
        val photoFarmIds = uploadPendingTaskPhotos(token)
        val appliedFarmIds = results
            .filter { it.status == "applied" }
            .mapNotNull { pendingFarmIdsByCommandId[it.clientCommandId] }
            .toSet()
        val refreshedSnapshots = if (refreshAfterSync) {
            refreshFarmIds(appliedFarmIds + photoFarmIds, token, activeFarmId)
        } else {
            emptyList()
        }
        val refreshed = activeFarmId?.let { active ->
            refreshedSnapshots.firstOrNull { it.farm.id == active }
        }
        return SyncSummary(
            results = results,
            remainingQueueCount = fieldStore.pendingCount(),
            refreshedSnapshot = refreshed,
            refreshedSnapshots = refreshedSnapshots,
        )
    }

    fun pendingCount(): Int = fieldStore.pendingCount()

    fun failedCommands(): List<OutboxFailure> = fieldStore.failedCommands()

    fun syncIntervalMinutes(): Int = fieldStore.getSyncIntervalMinutes()

    fun setSyncIntervalMinutes(value: Int) {
        fieldStore.setSyncIntervalMinutes(value)
    }

    private fun loadFarmAccess(token: String, preferredFarmId: String?): FarmLoadResult {
        val bootstrap = BootstrapResult.fromJson(apiClient.bootstrap(token))
        val farms = bootstrap.farms
        fieldStore.saveAvailableFarms(farms)

        val snapshotsByFarm = linkedMapOf<String, FarmSnapshot>()
        var failedFarmCount = 0
        farms.forEach { farm ->
            val snapshot = runCatching {
                refreshSnapshot(farm.id, token, makeActive = false)
            }.getOrElse {
                failedFarmCount += 1
                fieldStore.loadSnapshot(farm.id)
            }
            if (snapshot != null) {
                snapshotsByFarm[farm.id] = snapshot
            }
        }

        val activeFarm = farms.firstOrNull { it.id == preferredFarmId } ?: farms.firstOrNull()
        val activeSnapshot = activeFarm?.let { farm ->
            snapshotsByFarm[farm.id] ?: fieldStore.loadSnapshot(farm.id)
        }
        if (activeFarm == null) {
            fieldStore.clearActiveFarmId()
        } else {
            fieldStore.setActiveFarmId(activeFarm.id)
        }

        return FarmLoadResult(
            bootstrap = bootstrap,
            availableFarms = farms,
            activeFarm = activeFarm,
            snapshot = activeSnapshot,
            snapshots = snapshotsByFarm.values.toList(),
            prefetchedCount = snapshotsByFarm.size,
            failedFarmCount = failedFarmCount,
        )
    }

    private fun refreshSnapshot(farmId: String, token: String, makeActive: Boolean): FarmSnapshot {
        if (farmId.isBlank()) {
            error("No farm is available to refresh.")
        }
        val snapshot = FarmSnapshot.fromJson(apiClient.farmSnapshot(token, farmId))
        fieldStore.saveSnapshot(snapshot, makeActive = makeActive)
        return fieldStore.loadSnapshot(farmId) ?: snapshot
    }

    private fun refreshFarmIds(farmIds: Set<String>, token: String, activeFarmId: String?): List<FarmSnapshot> {
        return farmIds
            .filter { it.isNotBlank() }
            .distinct()
            .mapNotNull { farmId ->
                runCatching {
                    refreshSnapshot(farmId, token, makeActive = farmId == activeFarmId)
                }.getOrNull()
            }
    }

    private fun commandFarmIds(commands: JSONArray): Map<String, String> = buildMap {
        for (index in 0 until commands.length()) {
            val command = commands.optJSONObject(index) ?: continue
            val commandId = command.optString("client_command_id")
            val farmId = command.optString("farm_id")
            if (commandId.isNotBlank() && farmId.isNotBlank()) {
                put(commandId, farmId)
            }
        }
    }

    private fun uploadPendingTaskPhotos(token: String): Set<String> {
        val uploadedFarmIds = mutableSetOf<String>()
        fieldStore.pendingTaskPhotos().forEach { photo ->
            val error = runCatching { apiClient.uploadTaskAttachment(token, photo) }.exceptionOrNull()
            if (error == null) {
                fieldStore.markTaskPhotoUploaded(photo.clientAttachmentId)
                uploadedFarmIds.add(photo.farmId)
            } else {
                fieldStore.markTaskPhotoFailed(photo.clientAttachmentId, error.message ?: "Photo upload failed")
            }
        }
        return uploadedFarmIds
    }
}

data class LogoutResult(
    val serverRevoked: Boolean,
    val errorMessage: String?,
)
