package com.agritrack.mobile.data

import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.abs
import kotlin.math.floor
import kotlin.math.round

internal fun FarmSnapshot.withOptimisticCommands(commands: JSONArray): FarmSnapshot {
    if (commands.length() == 0) {
        return this
    }
    val json = JSONObject(rawJson)
    var changed = false
    var rebuildGrazing = false
    for (index in 0 until commands.length()) {
        val command = commands.optJSONObject(index) ?: continue
        if (command.optString("farm_id") != farm.id) {
            continue
        }
        val payload = command.optJSONObject("payload") ?: JSONObject()
        when (command.optString("type")) {
            "rainfall.create" -> changed = applyRainfallCreate(json, command, payload) || changed
            "mob.create" -> changed = applyMobCreate(json, command, payload) || changed
            "mob_event.create" -> changed = applyMobEventCreate(json, command, payload) || changed
            "paddock_event.create" -> changed = applyPaddockEventCreate(json, command, payload) || changed
            "water_asset_event.create" -> changed = applyWaterAssetEventCreate(json, command, payload) || changed
            "fence_event.create" -> changed = applyFenceEventCreate(json, command, payload) || changed
            "stock_count.record" -> {
                if (applyStockCount(json, command, payload)) {
                    changed = true
                    rebuildGrazing = true
                }
            }
            "mob.move" -> {
                if (applyMobMove(json, command, payload)) {
                    changed = true
                    rebuildGrazing = true
                }
            }
            "mob.transfer" -> {
                if (applyMobTransfer(json, command, payload)) {
                    changed = true
                    rebuildGrazing = true
                }
            }
            "paddock.update" -> changed = applyPaddockUpdate(json, payload) || changed
            "gate.update" -> {
                if (applyGateUpdate(json, command, payload)) {
                    changed = true
                    rebuildGrazing = true
                }
            }
            "water_asset_status.update" -> changed = applyWaterAssetStatus(json, payload) || changed
            "fence_section.update" -> changed = applyFenceSectionUpdate(json, payload) || changed
            "shearer.create" -> changed = applyShearerCreate(json, command, payload) || changed
            "shearing_session.create" -> changed = applyShearingSessionCreate(json, command, payload) || changed
            "shearing_session.update" -> changed = applyShearingSessionUpdate(json, payload) || changed
            "shearing_entry.record" -> changed = applyShearingEntryRecord(json, command, payload) || changed
            "shearing_bale_code.upsert" -> changed = applyShearingBaleCodeUpsert(json, command, payload) || changed
            "shearing_bale.record" -> changed = applyShearingBaleRecord(json, command, payload) || changed
            "shearing_bale.delete" -> changed = applyShearingBaleDelete(json, payload) || changed
            "task.create" -> changed = applyTaskCreate(json, command, payload) || changed
            "task.status.update" -> changed = applyTaskStatus(json, command, payload) || changed
            "task.comment.create" -> changed = applyTaskComment(json, command, payload) || changed
        }
    }
    if (!changed) {
        return this
    }
    if (rebuildGrazing) {
        rebuildGrazingByPaddock(json)
    }
    return FarmSnapshot.fromJson(json)
}

internal fun FarmSnapshot.withOptimisticTaskPhotos(photos: JSONArray): FarmSnapshot {
    if (photos.length() == 0) {
        return this
    }
    val json = JSONObject(rawJson)
    var changed = false
    for (index in 0 until photos.length()) {
        val photo = photos.optJSONObject(index) ?: continue
        if (photo.optString("farm_id") != farm.id) {
            continue
        }
        changed = applyTaskPhoto(json, photo) || changed
    }
    return if (changed) FarmSnapshot.fromJson(json) else this
}

private fun applyRainfallCreate(json: JSONObject, command: JSONObject, payload: JSONObject): Boolean {
    val recordedOn = payload.optString("recorded_on")
    if (recordedOn.isBlank()) {
        return false
    }
    prependObject(
        json,
        "rainfall",
        JSONObject()
            .put("id", pendingId(command, "rainfall"))
            .put("farm_id", command.optString("farm_id"))
            .put("recorded_on", recordedOn)
            .put("mm", payload.optDouble("mm", 0.0))
            .put("source", payload.optString("source", "mobile"))
            .putOptional("note", payload.optionalString("note"))
            .put("pending_sync", true),
    )
    return true
}

private fun applyMobCreate(json: JSONObject, command: JSONObject, payload: JSONObject): Boolean {
    val name = payload.optString("name").trim()
    if (name.isBlank()) {
        return false
    }
    upsertObjectById(
        json,
        "mobs",
        JSONObject()
            .put("id", pendingId(command, "mob"))
            .put("farm_id", command.optString("farm_id"))
            .put("name", name)
            .put("status", "active")
            .putOptional("origin_note", payload.optionalString("origin_note"))
            .put("balances", JSONArray())
            .put("pending_sync", true),
    )
    return true
}

private fun applyMobEventCreate(json: JSONObject, command: JSONObject, payload: JSONObject): Boolean {
    val mobId = payload.optString("mob_id")
    val description = payload.optString("description").trim()
    if (mobId.isBlank() || description.isBlank()) {
        return false
    }
    prependObject(
        json,
        "mob_events",
        JSONObject()
            .put("id", pendingId(command, "mob-event"))
            .put("farm_id", command.optString("farm_id"))
            .put("mob_id", mobId)
            .putOptional("event_at", payload.optionalString("event_at"))
            .put("tags", copyArray(payload.optJSONArray("tags")))
            .put("description", description)
            .put("attachment_count", 0)
            .put("attachments", JSONArray())
            .put("pending_sync", true),
    )
    return true
}

private fun applyPaddockEventCreate(json: JSONObject, command: JSONObject, payload: JSONObject): Boolean {
    val paddockId = payload.optString("paddock_id")
    val description = payload.optString("description").trim()
    if (paddockId.isBlank() || description.isBlank()) {
        return false
    }
    prependObject(
        json,
        "paddock_events",
        JSONObject()
            .put("id", pendingId(command, "paddock-event"))
            .put("farm_id", command.optString("farm_id"))
            .put("paddock_id", paddockId)
            .putOptional("event_at", payload.optionalString("event_at"))
            .put("tags", copyArray(payload.optJSONArray("tags")))
            .put("description", description)
            .put("attachment_count", 0)
            .put("attachments", JSONArray())
            .put("pending_sync", true),
    )
    return true
}

private fun applyWaterAssetEventCreate(json: JSONObject, command: JSONObject, payload: JSONObject): Boolean {
    val waterAssetId = payload.optString("water_asset_id")
    val description = payload.optString("description").trim()
    if (waterAssetId.isBlank() || description.isBlank()) {
        return false
    }
    prependObject(
        json,
        "water_asset_events",
        JSONObject()
            .put("id", pendingId(command, "water-asset-event"))
            .put("farm_id", command.optString("farm_id"))
            .put("water_asset_id", waterAssetId)
            .putOptional("event_at", payload.optionalString("event_at"))
            .put("tags", copyArray(payload.optJSONArray("tags")))
            .put("description", description)
            .put("attachment_count", 0)
            .put("attachments", JSONArray())
            .put("pending_sync", true),
    )
    return true
}

private fun applyFenceEventCreate(json: JSONObject, command: JSONObject, payload: JSONObject): Boolean {
    val fenceSectionId = payload.optString("fence_section_id")
    val description = payload.optString("description").trim()
    if (fenceSectionId.isBlank() || description.isBlank()) {
        return false
    }
    val eventType = payload.optString("event_type", "inspection").ifBlank { "inspection" }
    val conditionAfter = payload.optionalString("condition_after")
    prependObject(
        json,
        "fence_events",
        JSONObject()
            .put("id", pendingId(command, "fence-event"))
            .put("farm_id", command.optString("farm_id"))
            .put("fence_section_id", fenceSectionId)
            .putOptional("event_at", payload.optionalString("event_at"))
            .put("event_type", eventType)
            .put("event_type_label", labelFromValue(eventType))
            .put("tags", copyArray(payload.optJSONArray("tags")))
            .putOptional("condition_after", conditionAfter)
            .putOptional("condition_after_label", conditionAfter?.let(::labelFromValue))
            .put("description", description)
            .put("materials", copyArray(payload.optJSONArray("materials")))
            .put("attachment_count", 0)
            .put("attachments", JSONArray())
            .put("pending_sync", true),
    )
    conditionAfter?.let { updateFenceCondition(json, fenceSectionId, it) }
    return true
}

private fun applyStockCount(json: JSONObject, command: JSONObject, payload: JSONObject): Boolean {
    val mob = findObjectById(json.optJSONArray("mobs"), payload.optString("mob_id")) ?: return false
    val quantity = payload.optInt("quantity", -1)
    if (quantity < 0) {
        return false
    }
    val groupId = payload.optString("animal_group_type_id").ifBlank {
        pendingId(command, "animal-group")
    }
    val group = payload.optJSONObject("animal_group_type")
        ?.deepCopy()
        ?.put("id", groupId)
        ?: animalGroupTypeForId(json, groupId)
        ?: JSONObject().put("id", groupId)
    addOrUpdateBalance(
        mob = mob,
        groupId = groupId,
        group = group,
        quantity = quantity,
        balanceId = pendingId(command, "balance"),
    )
    return true
}

private fun applyMobMove(json: JSONObject, command: JSONObject, payload: JSONObject): Boolean {
    val mobId = payload.optString("mob_id")
    val allocations = payload.optJSONArray("allocations") ?: return false
    if (mobId.isBlank() || allocations.length() == 0) {
        return false
    }
    var sessionAllocations = copyArray(allocations)
    if (payload.optString("allocation_mode") == "counts" || hasGroupCountAllocations(sessionAllocations)) {
        val mob = findObjectById(json.optJSONArray("mobs"), mobId)
        if (mob != null) {
            for (index in 0 until sessionAllocations.length()) {
                val allocation = sessionAllocations.optJSONObject(index) ?: continue
                allocation.put("allocation_fraction", optimisticAllocationFraction(allocation, mob))
            }
        }
    } else {
        sessionAllocations = openGateAdjustedAllocations(json, sessionAllocations)
    }
    val retained = mutableListOf<JSONObject>()
    val activeGrazing = ensureArray(json, "active_grazing")
    for (index in 0 until activeGrazing.length()) {
        val item = activeGrazing.optJSONObject(index) ?: continue
        if (item.optString("mob_id") != mobId) {
            retained.add(item)
        }
    }
    val session = JSONObject()
        .put("id", pendingId(command, "grazing"))
        .put("farm_id", command.optString("farm_id"))
        .put("mob_id", mobId)
        .putOptional("start_at", payload.optionalString("event_time"))
        .put("allocations", sessionAllocations)
        .put("pending_sync", true)
    retained.add(0, session)
    json.put("active_grazing", JSONArray().apply { retained.forEach(::put) })
    return true
}

private fun applyMobTransfer(json: JSONObject, command: JSONObject, payload: JSONObject): Boolean {
    val sourceMob = findObjectById(json.optJSONArray("mobs"), payload.optString("source_mob_id")) ?: return false
    val destinationMob = findObjectById(json.optJSONArray("mobs"), payload.optString("destination_mob_id"))
    val transfers = payload.optJSONArray("transfers") ?: return false
    var changed = false
    for (index in 0 until transfers.length()) {
        val transfer = transfers.optJSONObject(index) ?: continue
        val groupId = transfer.optString("animal_group_type_id")
        val quantity = transfer.optInt("quantity", 0)
        if (groupId.isBlank() || quantity <= 0) {
            continue
        }
        val sourceBalance = findBalance(sourceMob, groupId)
        val group = sourceBalance?.optJSONObject("animal_group_type")?.deepCopy()
            ?: animalGroupTypeForId(json, groupId)
            ?: JSONObject().put("id", groupId)
        if (sourceBalance != null) {
            sourceBalance.put("head_count", (sourceBalance.optInt("head_count", 0) - quantity).coerceAtLeast(0))
            changed = true
        }
        if (destinationMob != null) {
            val destinationBalance = findBalance(destinationMob, groupId)
            if (destinationBalance == null) {
                addOrUpdateBalance(
                    mob = destinationMob,
                    groupId = groupId,
                    group = group,
                    quantity = quantity,
                    balanceId = pendingId(command, "balance"),
                )
            } else {
                destinationBalance.put("head_count", destinationBalance.optInt("head_count", 0) + quantity)
            }
            changed = true
        }
    }
    return changed
}

private fun applyPaddockUpdate(json: JSONObject, payload: JSONObject): Boolean {
    val paddock = findObjectById(json.optJSONArray("paddocks"), payload.optString("paddock_id")) ?: return false
    if (payload.has("status")) {
        paddock.put("status", payload.optString("status"))
    }
    if (payload.has("notes")) {
        paddock.putOptional("notes", payload.optionalString("notes"))
    }
    if (payload.has("tags")) {
        paddock.put("tags", copyArray(payload.optJSONArray("tags")))
    }
    paddock.put("pending_sync", true)
    return true
}

private fun applyGateUpdate(json: JSONObject, command: JSONObject, payload: JSONObject): Boolean {
    val gateId = payload.optString("gate_id")
    val status = payload.optString("status").trim().lowercase()
    if (gateId.isBlank() || status !in setOf("open", "closed")) {
        return false
    }

    val gate = findObjectById(json.optJSONArray("gates"), gateId)
    var changed = false
    if (gate != null) {
        changed = redistributeGateAllocations(json, gate, status, payload) || changed
        gate.put("status", status)
        gate.putOptional("last_state_changed_at", payload.optionalString("event_time"))
        gate.put("pending_sync", true)
        changed = true
    }

    val mapFeatures = json.optJSONArray("map_features") ?: JSONArray()
    for (index in 0 until mapFeatures.length()) {
        val feature = mapFeatures.optJSONObject(index) ?: continue
        val properties = feature.optJSONObject("properties") ?: continue
        val featureGateId = properties.optString("gate_id", properties.optString("id"))
        if (properties.optString("feature_type") != "gate" || featureGateId != gateId) {
            continue
        }
        properties.put("status", status)
        properties.put("pending_sync", true)
        changed = true
    }
    if (changed && gate == null) {
        prependObject(
            json,
            "gates",
            JSONObject()
                .put("id", gateId)
                .put("gate_id", gateId)
                .put("farm_id", command.optString("farm_id"))
                .put("status", status)
                .put("active", true)
                .put("source", "manual")
                .put("name", "Gate")
                .put("pending_sync", true),
        )
    }
    return changed
}

private fun redistributeGateAllocations(json: JSONObject, gate: JSONObject, targetStatus: String, payload: JSONObject): Boolean {
    val currentStatus = gate.optString("status", "closed").lowercase()
    if (currentStatus == targetStatus) {
        return false
    }
    return when (targetStatus) {
        "open" -> redistributeForGateOpen(json, gate, payload)
        "closed" -> redistributeForGateClose(json, gate, payload)
        else -> false
    }
}

private fun redistributeForGateOpen(json: JSONObject, gate: JSONObject, payload: JSONObject): Boolean {
    val (left, _) = gatePaddockPair(gate) ?: return false
    val allPaddockIds = activePaddockIds(json)
    if (allPaddockIds.isEmpty()) {
        return false
    }
    val component = componentContaining(
        components(allPaddockIds, openGateEdges(json, gate.optString("id"), "open")),
        left,
    )
    if (component.isEmpty()) {
        return false
    }
    var changed = false
    val activeGrazing = json.optJSONArray("active_grazing") ?: JSONArray()
    for (index in 0 until activeGrazing.length()) {
        val session = activeGrazing.optJSONObject(index) ?: continue
        val current = allocationMap(session)
        val totalInComponent = current
            .filterKeys { it in component }
            .values
            .sum()
        if (totalInComponent <= 0.0) {
            continue
        }
        val target = current
            .filterKeys { it !in component }
            .toMutableMap()
        target.putAll(areaWeightedAllocations(json, component, totalInComponent))
        changed = replaceSessionAllocations(session, target, payload.optionalString("event_time")) || changed
    }
    return changed
}

private fun redistributeForGateClose(json: JSONObject, gate: JSONObject, payload: JSONObject): Boolean {
    val (left, _) = gatePaddockPair(gate) ?: return false
    val allPaddockIds = activePaddockIds(json)
    if (allPaddockIds.isEmpty()) {
        return false
    }
    val beforeComponent = componentContaining(components(allPaddockIds, openGateEdges(json)), left)
    val afterComponents = components(
        beforeComponent,
        openGateEdges(json, gate.optString("id"), "closed"),
    )
    if (afterComponents.size <= 1) {
        return false
    }
    val componentIndexByPaddock = mutableMapOf<String, Int>()
    afterComponents.forEachIndexed { index, component ->
        component.forEach { paddockId -> componentIndexByPaddock[paddockId] = index }
    }
    val choices = gateClosureChoiceMap(payload.optJSONArray("closure_choices"))
    var changed = false
    val activeGrazing = json.optJSONArray("active_grazing") ?: JSONArray()
    for (index in 0 until activeGrazing.length()) {
        val session = activeGrazing.optJSONObject(index) ?: continue
        val current = allocationMap(session)
        val totalInPrevious = current
            .filterKeys { it in beforeComponent }
            .values
            .sum()
        if (totalInPrevious <= 0.0) {
            continue
        }
        val componentIndexes = current
            .filter { (paddockId, fraction) -> fraction > 0.0 && paddockId in componentIndexByPaddock }
            .mapNotNull { (paddockId, _) -> componentIndexByPaddock[paddockId] }
            .toSet()
        val targetComponent = when {
            componentIndexes.size > 1 -> {
                val selectedPaddockId = choices[session.optString("mob_id")]
                val selectedIndex = componentIndexByPaddock[selectedPaddockId]
                if (selectedIndex == null) continue
                afterComponents[selectedIndex]
            }
            componentIndexes.isNotEmpty() -> afterComponents[componentIndexes.first()]
            else -> afterComponents.first()
        }
        val target = current
            .filterKeys { it !in beforeComponent }
            .toMutableMap()
        target.putAll(areaWeightedAllocations(json, targetComponent, totalInPrevious))
        changed = replaceSessionAllocations(session, target, payload.optionalString("event_time")) || changed
    }
    return changed
}

private fun openGateAdjustedAllocations(json: JSONObject, allocations: JSONArray): JSONArray {
    val allPaddockIds = activePaddockIds(json)
    if (allPaddockIds.isEmpty()) {
        return allocations
    }
    val connectedComponents = components(allPaddockIds, openGateEdges(json))
        .filter { it.size > 1 }
    if (connectedComponents.isEmpty()) {
        return allocations
    }
    val componentByPaddock = mutableMapOf<String, Set<String>>()
    connectedComponents.forEach { component ->
        component.forEach { paddockId -> componentByPaddock[paddockId] = component }
    }

    val target = linkedMapOf<String, Double>()
    var changed = false
    for (index in 0 until allocations.length()) {
        val allocation = allocations.optJSONObject(index) ?: continue
        val paddockId = allocation.optString("paddock_id")
        if (paddockId.isBlank()) {
            return allocations
        }
        val fraction = allocation.optDouble("allocation_fraction", 0.0)
        val component = componentByPaddock[paddockId]
        if (component == null) {
            target[paddockId] = (target[paddockId] ?: 0.0) + fraction
            continue
        }
        areaWeightedAllocations(json, component, fraction).forEach { (targetPaddockId, targetFraction) ->
            target[targetPaddockId] = (target[targetPaddockId] ?: 0.0) + targetFraction
        }
        changed = true
    }
    if (!changed) {
        return allocations
    }
    return JSONArray().apply {
        normalizeAllocationMap(target).toSortedMap().forEach { (paddockId, fraction) ->
            if (fraction > 0.0) {
                put(JSONObject().put("paddock_id", paddockId).put("allocation_fraction", fraction))
            }
        }
    }
}

private fun hasGroupCountAllocations(allocations: JSONArray): Boolean {
    for (index in 0 until allocations.length()) {
        val groupCounts = allocations.optJSONObject(index)?.optJSONArray("group_counts")
        if (groupCounts != null && groupCounts.length() > 0) {
            return true
        }
    }
    return false
}

private fun activePaddockIds(json: JSONObject): Set<String> {
    val paddocks = json.optJSONArray("paddocks") ?: JSONArray()
    val ids = mutableSetOf<String>()
    for (index in 0 until paddocks.length()) {
        val paddock = paddocks.optJSONObject(index) ?: continue
        if (paddock.optString("status", "active") == "active") {
            paddock.optString("id").takeIf { it.isNotBlank() }?.let(ids::add)
        }
    }
    return ids
}

private fun gatePaddockPair(gate: JSONObject): Pair<String, String>? {
    val left = gate.optString("paddock_a_id")
    val right = gate.optString("paddock_b_id")
    if (left.isBlank() || right.isBlank()) {
        return null
    }
    return left to right
}

private fun openGateEdges(
    json: JSONObject,
    overrideGateId: String? = null,
    overrideStatus: String? = null,
): List<Pair<String, String>> {
    val gates = json.optJSONArray("gates") ?: JSONArray()
    val edges = mutableListOf<Pair<String, String>>()
    for (index in 0 until gates.length()) {
        val gate = gates.optJSONObject(index) ?: continue
        if (!gate.optBoolean("active", true)) {
            continue
        }
        val gateId = gate.optString("id", gate.optString("gate_id"))
        val status = if (overrideGateId != null && gateId == overrideGateId) {
            overrideStatus ?: gate.optString("status")
        } else {
            gate.optString("status")
        }
        if (status != "open") {
            continue
        }
        gatePaddockPair(gate)?.let(edges::add)
    }
    return edges
}

private fun components(paddockIds: Set<String>, edges: List<Pair<String, String>>): List<Set<String>> {
    val adjacency = paddockIds.associateWith { mutableSetOf<String>() }
    edges.forEach { (left, right) ->
        if (left in adjacency && right in adjacency) {
            adjacency[left]?.add(right)
            adjacency[right]?.add(left)
        }
    }
    val seen = mutableSetOf<String>()
    val result = mutableListOf<Set<String>>()
    paddockIds.sorted().forEach { paddockId ->
        if (!seen.add(paddockId)) {
            return@forEach
        }
        val stack = mutableListOf(paddockId)
        val component = mutableSetOf<String>()
        while (stack.isNotEmpty()) {
            val current = stack.removeAt(stack.lastIndex)
            component.add(current)
            adjacency[current].orEmpty().forEach { neighbor ->
                if (seen.add(neighbor)) {
                    stack.add(neighbor)
                }
            }
        }
        result.add(component)
    }
    return result
}

private fun componentContaining(components: List<Set<String>>, paddockId: String): Set<String> =
    components.firstOrNull { paddockId in it } ?: setOf(paddockId)

private fun areaWeightedAllocations(json: JSONObject, paddockIds: Set<String>, totalFraction: Double): Map<String, Double> {
    val paddocks = json.optJSONArray("paddocks") ?: JSONArray()
    val rows = mutableListOf<JSONObject>()
    for (index in 0 until paddocks.length()) {
        val paddock = paddocks.optJSONObject(index) ?: continue
        if (paddock.optString("id") in paddockIds) {
            rows.add(paddock)
        }
    }
    val sorted = rows.sortedBy { it.optString("name").lowercase() }
    val splits = splitFractionByRatios(
        totalFraction = totalFraction,
        ratios = sorted.map { paddock ->
            val grazeable = paddock.optDouble("grazeable_area_ha", 0.0)
            if (grazeable > 0.0) grazeable else paddock.optDouble("area_ha", 0.0).coerceAtLeast(0.0)
        },
    )
    return sorted
        .zip(splits)
        .filter { (_, fraction) -> fraction > 0.0 }
        .associate { (paddock, fraction) -> paddock.optString("id") to fraction }
}

private fun splitFractionByRatios(totalFraction: Double, ratios: List<Double>): List<Double> {
    if (totalFraction <= 0.0 || ratios.isEmpty()) {
        return ratios.map { 0.0 }
    }
    var positiveRatios = ratios.map { it.coerceAtLeast(0.0) }
    var positiveTotal = positiveRatios.sum()
    if (positiveTotal <= 0.0) {
        positiveRatios = ratios.map { 1.0 }
        positiveTotal = positiveRatios.sum()
    }
    val totalUnits = round(totalFraction * 10000.0).toInt()
    val units = MutableList(ratios.size) { 0 }
    val remainders = mutableListOf<Pair<Double, Int>>()
    var assigned = 0
    positiveRatios.forEachIndexed { index, ratio ->
        val rawUnits = totalUnits * (ratio / positiveTotal)
        val floorUnits = floor(rawUnits).toInt()
        units[index] = floorUnits
        assigned += floorUnits
        remainders.add((rawUnits - floorUnits) to index)
    }
    var remaining = totalUnits - assigned
    remainders.sortedWith(compareBy<Pair<Double, Int>> { -it.first }.thenBy { it.second }).forEach { (_, index) ->
        if (remaining <= 0) {
            return@forEach
        }
        if (positiveRatios[index] <= 0.0) {
            return@forEach
        }
        units[index] += 1
        remaining -= 1
    }
    return units.map { it / 10000.0 }
}

private fun allocationMap(session: JSONObject): Map<String, Double> {
    val allocations = session.optJSONArray("allocations") ?: JSONArray()
    val result = linkedMapOf<String, Double>()
    for (index in 0 until allocations.length()) {
        val allocation = allocations.optJSONObject(index) ?: continue
        val paddockId = allocation.optString("paddock_id")
        if (paddockId.isNotBlank()) {
            result[paddockId] = allocation.optDouble("allocation_fraction", 0.0)
        }
    }
    return result
}

private fun replaceSessionAllocations(
    session: JSONObject,
    target: Map<String, Double>,
    eventTime: String?,
): Boolean {
    val normalized = normalizeAllocationMap(target)
    if (allocationMapsEqual(allocationMap(session), normalized)) {
        return false
    }
    session.put(
        "allocations",
        JSONArray().apply {
            normalized.toSortedMap().forEach { (paddockId, fraction) ->
                if (fraction > 0.0) {
                    put(JSONObject().put("paddock_id", paddockId).put("allocation_fraction", fraction))
                }
            }
        },
    )
    eventTime?.let { session.put("start_at", it) }
    session.put("pending_sync", true)
    return true
}

private fun normalizeAllocationMap(target: Map<String, Double>): Map<String, Double> {
    val positive = target
        .filter { (_, fraction) -> fraction > 0.0 }
        .mapValues { (_, fraction) -> round(fraction * 10000.0) / 10000.0 }
        .toMutableMap()
    if (positive.isEmpty()) {
        return positive
    }
    val sum = positive.values.sum()
    val diff = round((1.0 - sum) * 10000.0) / 10000.0
    if (abs(diff) > 0.0) {
        val firstKey = positive.keys.sorted().first()
        positive[firstKey] = round(((positive[firstKey] ?: 0.0) + diff) * 10000.0) / 10000.0
    }
    return positive
}

private fun allocationMapsEqual(left: Map<String, Double>, right: Map<String, Double>): Boolean {
    if (left.keys != right.keys) {
        return false
    }
    return left.all { (key, value) -> abs(value - (right[key] ?: 0.0)) < 0.00005 }
}

private fun gateClosureChoiceMap(choices: JSONArray?): Map<String, String> {
    if (choices == null) {
        return emptyMap()
    }
    val result = mutableMapOf<String, String>()
    for (index in 0 until choices.length()) {
        val choice = choices.optJSONObject(index) ?: continue
        val mobId = choice.optString("mob_id")
        val componentPaddockId = choice.optString("component_paddock_id")
        if (mobId.isNotBlank() && componentPaddockId.isNotBlank()) {
            result[mobId] = componentPaddockId
        }
    }
    return result
}

private fun applyWaterAssetStatus(json: JSONObject, payload: JSONObject): Boolean {
    val asset = findObjectById(json.optJSONArray("water_assets"), payload.optString("water_asset_id")) ?: return false
    if (payload.has("active")) {
        asset.put("active", payload.optBoolean("active", true))
    }
    if (payload.has("status")) {
        asset.putOptional("status", payload.optionalString("status"))
    }
    if (payload.has("water_level")) {
        asset.putOptional("water_level", payload.optionalString("water_level"))
    }
    asset.put("pending_sync", true)
    return true
}

private fun applyFenceSectionUpdate(json: JSONObject, payload: JSONObject): Boolean {
    val fenceSectionId = payload.optString("fence_section_id")
    val section = findObjectById(json.optJSONArray("fence_sections"), fenceSectionId) ?: return false
    if (payload.has("condition")) {
        val condition = payload.optString("condition")
        section.put("condition", condition)
        section.put("condition_label", labelFromValue(condition))
    }
    if (payload.has("notes")) {
        section.putOptional("notes", payload.optionalString("notes"))
    }
    if (payload.has("electric_wire")) {
        section.put("electric_wire", payload.optBoolean("electric_wire", false))
    }
    section.put("pending_sync", true)
    updateFenceMapFeature(json, fenceSectionId, payload)
    return true
}

private fun updateFenceCondition(json: JSONObject, fenceSectionId: String, condition: String) {
    findObjectById(json.optJSONArray("fence_sections"), fenceSectionId)?.let { section ->
        section.put("condition", condition)
        section.put("condition_label", labelFromValue(condition))
        section.put("pending_sync", true)
    }
    updateFenceMapFeature(
        json,
        fenceSectionId,
        JSONObject().put("condition", condition),
    )
}

private fun updateFenceMapFeature(json: JSONObject, fenceSectionId: String, payload: JSONObject) {
    val mapFeatures = json.optJSONArray("map_features") ?: return
    for (index in 0 until mapFeatures.length()) {
        val feature = mapFeatures.optJSONObject(index) ?: continue
        val properties = feature.optJSONObject("properties") ?: continue
        val featureFenceId = properties.optString("fence_section_id", properties.optString("id"))
        if (properties.optString("feature_type") != "fence_section" || featureFenceId != fenceSectionId) {
            continue
        }
        if (payload.has("condition")) {
            val condition = payload.optString("condition")
            properties.put("condition", condition)
            properties.put("condition_label", labelFromValue(condition))
        }
        if (payload.has("notes")) {
            properties.putOptional("notes", payload.optionalString("notes"))
        }
        if (payload.has("electric_wire")) {
            properties.put("electric_wire", payload.optBoolean("electric_wire", false))
        }
        properties.put("pending_sync", true)
    }
}

private fun applyShearerCreate(json: JSONObject, command: JSONObject, payload: JSONObject): Boolean {
    val name = payload.optString("name").trim()
    if (name.isBlank()) return false
    upsertObjectById(
        json,
        "shearers",
        JSONObject()
            .put("id", payload.optString("id").ifBlank { pendingId(command, "shearer") })
            .put("farm_id", command.optString("farm_id"))
            .put("name", name)
            .put("active", payload.optBoolean("active", true))
            .put("pending_sync", true),
    )
    return true
}

private fun applyShearingSessionCreate(json: JSONObject, command: JSONObject, payload: JSONObject): Boolean {
    val name = payload.optString("name").trim()
    val species = payload.optString("species").ifBlank { "Sheep" }
    val startDate = payload.optString("start_date")
    if (name.isBlank() || startDate.isBlank()) return false
    val session = JSONObject()
        .put("id", payload.optString("id").ifBlank { pendingId(command, "shearing-session") })
        .put("farm_id", command.optString("farm_id"))
        .put("name", name)
        .put("species", species)
        .put("start_date", startDate)
        .putOptional("end_date", payload.optionalString("end_date"))
        .put("status", payload.optString("status", "open").ifBlank { "open" })
        .put("lootjie_rate", payload.optDouble("lootjie_rate", 0.0))
        .put("adult_old_ram_multiplier", payload.optDouble("adult_old_ram_multiplier", 2.0))
        .putOptional("notes", payload.optionalString("notes"))
        .put("entries", JSONArray())
        .put("bales", JSONArray())
        .put("pending_sync", true)
    rebuildShearingSessionTotals(session, json)
    upsertObjectById(json, "shearing_sessions", session)
    return true
}

private fun applyShearingSessionUpdate(json: JSONObject, payload: JSONObject): Boolean {
    val sessionId = payload.optString("session_id", payload.optString("id"))
    val session = findObjectById(json.optJSONArray("shearing_sessions"), sessionId) ?: return false
    listOf("name", "species", "start_date", "status", "notes").forEach { field ->
        if (payload.has(field)) {
            session.putOptional(field, payload.optionalString(field))
        }
    }
    if (payload.has("end_date")) {
        session.putOptional("end_date", payload.optionalString("end_date"))
    }
    if (payload.has("lootjie_rate")) {
        session.put("lootjie_rate", payload.optDouble("lootjie_rate", 0.0))
    }
    session.put("pending_sync", true)
    rebuildShearingSessionTotals(session, json)
    return true
}

private fun applyShearingEntryRecord(json: JSONObject, command: JSONObject, payload: JSONObject): Boolean {
    val session = findObjectById(json.optJSONArray("shearing_sessions"), payload.optString("session_id")) ?: return false
    val workDate = payload.optString("work_date")
    val shearerId = payload.optString("shearer_id")
    val groupId = payload.optString("animal_group_type_id").ifBlank { pendingId(command, "animal-group") }
    if (workDate.isBlank() || shearerId.isBlank()) return false
    val quantity = payload.optInt("quantity", -1)
    if (quantity < 0) return false
    val entries = ensureArray(session, "entries")
    val existingIndex = findShearingEntryIndex(entries, workDate, shearerId, groupId)
    if (quantity == 0) {
        if (existingIndex >= 0) {
            val retained = JSONArray()
            for (index in 0 until entries.length()) {
                if (index != existingIndex) retained.put(entries.get(index))
            }
            session.put("entries", retained)
            rebuildShearingSessionTotals(session, json)
            session.put("pending_sync", true)
            return true
        }
        return false
    }
    val group = payload.optJSONObject("animal_group_type")
        ?.deepCopy()
        ?.put("id", groupId)
        ?: JSONObject()
            .put("id", groupId)
            .put("species", session.optString("species", "Sheep"))
            .put("breed", "Unknown")
            .put("sex", "mixed")
            .put("age_class", "adult")
    val entry = JSONObject()
        .put("id", payload.optString("id").ifBlank { pendingId(command, "shearing-entry") })
        .put("session_id", session.optString("id"))
        .put("work_date", workDate)
        .put("shearer_id", shearerId)
        .put("shearer_name", shearerName(json, shearerId))
        .put("animal_group_type_id", groupId)
        .put("animal_group_type", group)
        .put("quantity", quantity)
        .putOptional("note", payload.optionalString("note"))
        .put("pending_sync", true)
    decorateShearingEntry(entry, session)
    if (existingIndex >= 0) {
        entries.put(existingIndex, entry)
    } else {
        entries.put(entry)
    }
    rebuildShearingSessionTotals(session, json)
    session.put("pending_sync", true)
    return true
}

private fun applyShearingBaleCodeUpsert(json: JSONObject, command: JSONObject, payload: JSONObject): Boolean {
    val species = payload.optString("species", "Sheep").ifBlank { "Sheep" }
    val code = payload.optString("code").trim()
    if (code.isBlank()) return false
    val baleCode = JSONObject()
        .put("id", payload.optString("id", payload.optString("bale_code_id")).ifBlank { pendingId(command, "bale-code") })
        .put("species", species)
        .put("code", code)
        .put("active", payload.optBoolean("active", true))
        .putOptional("line_type", payload.optionalString("line_type"))
        .putOptional("age_group", payload.optionalString("age_group"))
        .putOptional("fineness_grade", payload.optionalString("fineness_grade"))
        .putOptional("length_code", payload.optionalString("length_code"))
        .putOptional("color", payload.optionalString("color"))
        .putOptional("vegetable_matter", payload.optionalString("vegetable_matter"))
        .putOptional("style_character", payload.optionalString("style_character"))
        .putOptional("consistency", payload.optionalString("consistency"))
        .putOptional("fault", payload.optionalString("fault"))
        .putOptional("description", payload.optionalString("description"))
        .putOptional("notes", payload.optionalString("notes"))
        .put("pending_sync", true)
    payload.optionalDouble("fineness_micron")?.let { baleCode.put("fineness_micron", it) }
    payload.optionalDouble("clean_yield_percent")?.let { baleCode.put("clean_yield_percent", it) }
    upsertObjectById(json, "shearing_bale_codes", baleCode)
    return true
}

private fun applyShearingBaleRecord(json: JSONObject, command: JSONObject, payload: JSONObject): Boolean {
    val session = findObjectById(json.optJSONArray("shearing_sessions"), payload.optString("session_id")) ?: return false
    val weight = payload.optionalDouble("weight_kg") ?: return false
    if (weight <= 0.0) return false
    val baleCodeId = payload.optionalString("bale_code_id")
    val baleCode = baleCodeId?.let { baleCodeForId(json, it) }
    if (baleCode != null && baleCode.optString("species") != session.optString("species", "Sheep")) {
        return false
    }
    val codeText = payload.optionalString("code_text")
        ?: payload.optionalString("code")
        ?: baleCode?.optString("code")?.takeIf { it.isNotBlank() }
        ?: return false
    val price = deriveOptimisticBalePrices(weight, payload) ?: return false
    val bale = JSONObject()
        .put("id", payload.optString("id", payload.optString("bale_id")).ifBlank { pendingId(command, "shearing-bale") })
        .put("session_id", session.optString("id"))
        .putOptional("bale_code_id", baleCodeId)
        .put("code", baleCode?.optString("code") ?: codeText)
        .put("code_text", codeText)
        .putOptional("bale_number", payload.optionalString("bale_number"))
        .put("weight_kg", roundKg(weight))
        .putOptional("notes", payload.optionalString("notes") ?: payload.optionalString("note"))
        .put("pricing_input_mode", price.mode)
        .put("pending_sync", true)
    price.pricePerKg?.let { bale.put("price_per_kg", roundRate(it)) }
    price.totalPrice?.let { bale.put("total_price", roundMoney(it)) }
    upsertObjectById(ensureArray(session, "bales"), bale)
    rebuildShearingBaleMoney(session, json)
    session.put("pending_sync", true)
    return true
}

private fun applyShearingBaleDelete(json: JSONObject, payload: JSONObject): Boolean {
    val session = findObjectById(json.optJSONArray("shearing_sessions"), payload.optString("session_id")) ?: return false
    val baleId = payload.optString("bale_id", payload.optString("id"))
    if (baleId.isBlank()) return false
    val bales = ensureArray(session, "bales")
    val retained = JSONArray()
    var removed = false
    for (index in 0 until bales.length()) {
        val bale = bales.optJSONObject(index) ?: continue
        if (bale.optString("id") == baleId) {
            removed = true
        } else {
            retained.put(bale)
        }
    }
    if (!removed) return false
    session.put("bales", retained)
    rebuildShearingBaleMoney(session, json)
    session.put("pending_sync", true)
    return true
}

private fun findShearingEntryIndex(entries: JSONArray, workDate: String, shearerId: String, groupId: String): Int {
    for (index in 0 until entries.length()) {
        val entry = entries.optJSONObject(index) ?: continue
        if (
            entry.optString("work_date") == workDate &&
            entry.optString("shearer_id") == shearerId &&
            entry.optString("animal_group_type_id") == groupId
        ) {
            return index
        }
    }
    return -1
}

private fun rebuildShearingSessionTotals(session: JSONObject, json: JSONObject) {
    val byShearer = linkedMapOf<String, JSONObject>()
    val byAnimal = linkedMapOf<String, JSONObject>()
    val byDate = linkedMapOf<String, JSONObject>()
    var totalQuantity = 0
    var totalAmount = 0.0
    val entries = ensureArray(session, "entries")
    for (index in 0 until entries.length()) {
        val entry = entries.optJSONObject(index) ?: continue
        if (entry.optString("shearer_name").isBlank()) {
            entry.put("shearer_name", shearerName(json, entry.optString("shearer_id")))
        }
        decorateShearingEntry(entry, session)
        val quantity = entry.optInt("quantity", 0)
        val amount = entry.optDouble("line_amount", 0.0)
        totalQuantity += quantity
        totalAmount += amount

        val shearerId = entry.optString("shearer_id")
        val shearer = byShearer.getOrPut(shearerId) {
            JSONObject()
                .put("shearer_id", shearerId)
                .put("shearer_name", entry.optString("shearer_name", "Shearer"))
                .put("quantity", 0)
                .put("amount", 0.0)
        }
        shearer.put("quantity", shearer.optInt("quantity", 0) + quantity)
        shearer.put("amount", roundMoney(shearer.optDouble("amount", 0.0) + amount))

        val groupId = entry.optString("animal_group_type_id")
        val animal = byAnimal.getOrPut(groupId) {
            JSONObject()
                .put("animal_group_type_id", groupId)
                .put("animal_group_type", entry.optJSONObject("animal_group_type")?.deepCopy() ?: JSONObject())
                .put("quantity", 0)
                .put("amount", 0.0)
        }
        animal.put("quantity", animal.optInt("quantity", 0) + quantity)
        animal.put("amount", roundMoney(animal.optDouble("amount", 0.0) + amount))

        val workDate = entry.optString("work_date")
        val day = byDate.getOrPut(workDate) {
            JSONObject()
                .put("work_date", workDate)
                .put("quantity", 0)
                .put("amount", 0.0)
        }
        day.put("quantity", day.optInt("quantity", 0) + quantity)
        day.put("amount", roundMoney(day.optDouble("amount", 0.0) + amount))
    }
    session.put("totals", JSONObject().put("quantity", totalQuantity).put("amount", roundMoney(totalAmount)))
    session.put("by_shearer", JSONArray().apply { byShearer.values.sortedBy { it.optString("shearer_name") }.forEach(::put) })
    session.put("by_animal_type", JSONArray().apply { byAnimal.values.forEach(::put) })
    session.put("by_date", JSONArray().apply { byDate.values.sortedBy { it.optString("work_date") }.forEach(::put) })
    rebuildShearingBaleMoney(session, json)
}

private fun rebuildShearingBaleMoney(session: JSONObject, json: JSONObject) {
    val byCode = linkedMapOf<String, JSONObject>()
    var totalBales = 0
    var totalKg = 0.0
    var pricedBales = 0
    var pricedKg = 0.0
    var totalPrice = 0.0
    val bales = ensureArray(session, "bales")
    for (index in 0 until bales.length()) {
        val bale = bales.optJSONObject(index) ?: continue
        val baleCode = bale.optionalString("bale_code_id")?.let { baleCodeForId(json, it) }
        if (bale.optString("code").isBlank()) {
            bale.put("code", baleCode?.optString("code") ?: bale.optString("code_text", "Code"))
        }
        val weight = bale.optDouble("weight_kg", 0.0).coerceAtLeast(0.0)
        totalBales += 1
        totalKg += weight
        val balePrice = bale.optionalDouble("total_price")
        if (balePrice != null) {
            pricedBales += 1
            pricedKg += weight
            totalPrice += balePrice
        }
        val key = bale.optionalString("bale_code_id") ?: "ad-hoc:${bale.optString("code_text").lowercase()}"
        val row = byCode.getOrPut(key) {
            JSONObject()
                .putOptional("bale_code_id", bale.optionalString("bale_code_id"))
                .put("code", baleCode?.optString("code") ?: bale.optString("code_text", "Code"))
                .put("code_text", bale.optString("code_text", "Code"))
                .put("bale_count", 0)
                .put("bales", 0)
                .put("kg", 0.0)
                .put("priced_kg", 0.0)
                .put("unpriced_bales", 0)
                .put("total_price", 0.0)
        }
        row.put("bale_count", row.optInt("bale_count", 0) + 1)
        row.put("bales", row.optInt("bales", 0) + 1)
        row.put("kg", roundKg(row.optDouble("kg", 0.0) + weight))
        if (balePrice == null) {
            row.put("unpriced_bales", row.optInt("unpriced_bales", 0) + 1)
        } else {
            row.put("priced_kg", roundKg(row.optDouble("priced_kg", 0.0) + weight))
            row.put("total_price", roundMoney(row.optDouble("total_price", 0.0) + balePrice))
        }
    }
    val average = if (pricedKg > 0.0) roundRate(totalPrice / pricedKg) else null
    session.put(
        "bale_money_totals",
        JSONObject()
            .put("total_bales", totalBales)
            .put("total_kg", roundKg(totalKg))
            .put("priced_bales", pricedBales)
            .put("priced_kg", roundKg(pricedKg))
            .put("unpriced_bales", totalBales - pricedBales)
            .put("total_price", roundMoney(totalPrice))
            .putOptional("average_price_per_kg", average),
    )
    session.put(
        "bale_summary_by_code",
        JSONArray().apply {
            byCode.values.sortedBy { it.optString("code").lowercase() }.forEach { row ->
                val rowPricedKg = row.optDouble("priced_kg", 0.0)
                row.putOptional(
                    "average_price_per_kg",
                    if (rowPricedKg > 0.0) roundRate(row.optDouble("total_price", 0.0) / rowPricedKg) else null,
                )
                put(row)
            }
        },
    )
}

private fun decorateShearingEntry(entry: JSONObject, session: JSONObject) {
    val group = entry.optJSONObject("animal_group_type") ?: JSONObject()
    val multiplier = if (
        group.optString("sex").lowercase() == "ram" &&
        group.optString("age_class").lowercase() in setOf("adult", "old")
    ) {
        session.optDouble("adult_old_ram_multiplier", 2.0)
    } else {
        1.0
    }
    val unitRate = session.optDouble("lootjie_rate", 0.0) * multiplier
    entry.put("multiplier", multiplier)
    entry.put("unit_rate", roundMoney(unitRate))
    entry.put("line_amount", roundMoney(unitRate * entry.optInt("quantity", 0)))
}

private fun shearerName(json: JSONObject, shearerId: String): String {
    val shearer = findObjectById(json.optJSONArray("shearers"), shearerId)
    return shearer?.optString("name")?.takeIf { it.isNotBlank() } ?: "Shearer"
}

private fun baleCodeForId(json: JSONObject, baleCodeId: String): JSONObject? =
    findObjectById(json.optJSONArray("shearing_bale_codes"), baleCodeId)

private fun deriveOptimisticBalePrices(weightKg: Double, payload: JSONObject): OptimisticBalePrice? {
    val pricePerKg = payload.optionalDouble("price_per_kg")
    val totalPrice = payload.optionalDouble("total_price")
    if (pricePerKg == null && totalPrice == null) {
        return OptimisticBalePrice(null, null, "unpriced")
    }
    if (pricePerKg != null && pricePerKg < 0.0) return null
    if (totalPrice != null && totalPrice < 0.0) return null
    if (pricePerKg != null && totalPrice == null) {
        return OptimisticBalePrice(roundRate(pricePerKg), roundMoney(pricePerKg * weightKg), "price_per_kg")
    }
    if (pricePerKg == null && totalPrice != null) {
        return OptimisticBalePrice(roundRate(totalPrice / weightKg), roundMoney(totalPrice), "total_price")
    }
    val expected = roundMoney((pricePerKg ?: 0.0) * weightKg)
    if (abs(expected - (totalPrice ?: 0.0)) > 0.01) {
        return null
    }
    return OptimisticBalePrice(roundRate(pricePerKg ?: 0.0), roundMoney(totalPrice ?: 0.0), "both")
}

private fun roundMoney(value: Double): Double = round(value * 100.0) / 100.0

private fun roundKg(value: Double): Double = round(value * 1000.0) / 1000.0

private fun roundRate(value: Double): Double = round(value * 10000.0) / 10000.0

private fun applyTaskCreate(json: JSONObject, command: JSONObject, payload: JSONObject): Boolean {
    val heading = payload.optString("heading", payload.optString("title")).trim()
    if (heading.isBlank()) {
        return false
    }
    val taskId = pendingId(command, "task")
    val description = payload.optString("description", heading).ifBlank { heading }
    val priority = payload.optString("priority", "high").ifBlank { "high" }
    val status = payload.optString("status", "todo").ifBlank { "todo" }
    val entityLinks = taskLinks(json, taskId, command, payload)
    val task = JSONObject()
        .put("id", taskId)
        .put("farm_id", command.optString("farm_id"))
        .put("display_key", "Pending")
        .put("heading", heading)
        .put("description", description)
        .put("tags", copyArray(payload.optJSONArray("tags")))
        .putOptional("reporter_name", payload.optionalString("reporter_name"))
        .putOptional("assignee_name", payload.optionalString("assignee_name"))
        .put("status", status)
        .put("status_label", taskStatusLabel(status))
        .put("priority", priority)
        .put("priority_label", taskPriorityLabel(priority))
        .putOptional("due_date", payload.optionalString("due_date"))
        .put("entity_links", entityLinks)
        .put("comment_count", 0)
        .put("comments", JSONArray())
        .put("attachment_count", 0)
        .put("attachments", JSONArray())
        .put("pending_sync", true)
    prependObject(json, "tasks", task)
    payload.optionalString("due_date")?.let { dueDate ->
        prependObject(
            json,
            "calendar_items",
            JSONObject()
                .put("kind", "task")
                .put("date", dueDate)
                .put("source_id", taskId)
                .put("task_id", taskId)
                .put("title", heading)
                .put("description", description)
                .put("stage", status)
                .put("stage_label", taskStatusLabel(status))
                .putOptional("assignee_name", payload.optionalString("assignee_name"))
                .put("tags", copyArray(payload.optJSONArray("tags")))
                .put("priority", priority)
                .put("priority_label", taskPriorityLabel(priority))
                .put("entity_links", copyArray(entityLinks))
                .put("pending_sync", true),
        )
    }
    return true
}

private fun applyTaskStatus(json: JSONObject, command: JSONObject, payload: JSONObject): Boolean {
    val taskId = payload.optString("task_id")
    val status = payload.optString("status")
    val task = findObjectById(json.optJSONArray("tasks"), taskId) ?: return false
    if (status.isBlank()) {
        return false
    }
    task.put("status", status)
    task.put("status_label", taskStatusLabel(status))
    task.put("pending_sync", true)
    updateCalendarTaskStatus(json, taskId, status)
    payload.optionalString("note")?.let { note ->
        appendTaskComment(task, command, payload, note)
    }
    return true
}

private fun applyTaskComment(json: JSONObject, command: JSONObject, payload: JSONObject): Boolean {
    val task = findObjectById(json.optJSONArray("tasks"), payload.optString("task_id")) ?: return false
    val body = payload.optString("body", payload.optString("note")).trim()
    if (body.isBlank()) {
        return false
    }
    appendTaskComment(task, command, payload, body)
    return true
}

private fun applyTaskPhoto(json: JSONObject, photo: JSONObject): Boolean {
    val taskId = photo.optionalString("server_task_id")
        ?: photo.optionalString("target_client_command_id")?.let { "pending-task-$it" }
        ?: return false
    val task = findObjectById(json.optJSONArray("tasks"), taskId) ?: return false
    val clientAttachmentId = photo.optString("client_attachment_id")
    if (clientAttachmentId.isBlank()) {
        return false
    }
    val attachments = ensureArray(task, "attachments")
    for (index in 0 until attachments.length()) {
        if (attachments.optJSONObject(index)?.optString("client_attachment_id") == clientAttachmentId) {
            return false
        }
    }
    val originalFilename = photo.optString("original_filename", "photo")
    prependObject(
        task,
        "attachments",
        JSONObject()
            .put("id", pendingPhotoId(photo))
            .put("task_id", taskId)
            .put("client_attachment_id", clientAttachmentId)
            .put("original_filename", originalFilename)
            .put("content_type", photo.optString("content_type", "image/jpeg"))
            .put("byte_size", photo.optLong("byte_size", 0L))
            .putOptional("caption", photo.optionalString("caption"))
            .putOptional("captured_at", photo.optionalString("captured_at"))
            .put("pending_sync", true),
    )
    task.put("attachment_count", task.optInt("attachment_count", attachments.length()) + 1)
    task.put("pending_sync", true)
    return true
}

private fun taskLinks(
    json: JSONObject,
    taskId: String,
    command: JSONObject,
    payload: JSONObject,
): JSONArray {
    val links = JSONArray()
    fun addLink(entityType: String, entityId: String) {
        if (entityId.isBlank()) {
            return
        }
        links.put(
            JSONObject()
                .put("id", "${pendingId(command, "task-link")}-$entityType-$entityId")
                .put("task_id", taskId)
                .put("entity_type", entityType)
                .put("entity_id", entityId)
                .putOptional("entity_name", entityName(json, entityType, entityId)),
        )
    }
    addLink("paddock", payload.optString("paddock_id"))
    addLink("mob", payload.optString("mob_id"))
    addLink("water_asset", payload.optString("water_asset_id"))
    addLink("fence_section", payload.optString("fence_section_id"))
    addLinks("paddock", payload.optJSONArray("paddock_ids"), ::addLink)
    addLinks("mob", payload.optJSONArray("mob_ids"), ::addLink)
    addLinks("water_asset", payload.optJSONArray("water_asset_ids"), ::addLink)
    addLinks("fence_section", payload.optJSONArray("fence_section_ids"), ::addLink)
    return links
}

private fun addLinks(entityType: String, entityIds: JSONArray?, addLink: (String, String) -> Unit) {
    if (entityIds == null) {
        return
    }
    for (index in 0 until entityIds.length()) {
        addLink(entityType, entityIds.optString(index))
    }
}

private fun appendTaskComment(task: JSONObject, command: JSONObject, payload: JSONObject, body: String) {
    ensureArray(task, "comments").put(
        JSONObject()
            .put("id", pendingId(command, "task-comment"))
            .put("task_id", task.optString("id"))
            .put("author_name", payload.optString("author_name", payload.optString("changed_by_name", "Mobile user")))
            .put("body", body)
            .putOptional("created_at", payload.optionalString("changed_at"))
            .put("pending_sync", true),
    )
    task.put("comment_count", task.optInt("comment_count", 0) + 1)
    task.put("pending_sync", true)
}

private fun updateCalendarTaskStatus(json: JSONObject, taskId: String, status: String) {
    val calendarItems = json.optJSONArray("calendar_items") ?: return
    for (index in 0 until calendarItems.length()) {
        val item = calendarItems.optJSONObject(index) ?: continue
        if (item.optString("task_id") == taskId || item.optString("source_id") == taskId) {
            item.put("stage", status)
            item.put("stage_label", taskStatusLabel(status))
            item.put("pending_sync", true)
        }
    }
}

private fun rebuildGrazingByPaddock(json: JSONObject) {
    if (!json.has("active_grazing")) {
        return
    }
    val mobsById = mutableMapOf<String, JSONObject>()
    val mobs = json.optJSONArray("mobs") ?: JSONArray()
    for (index in 0 until mobs.length()) {
        val mob = mobs.optJSONObject(index) ?: continue
        if (mob.optString("status", "active") == "active") {
            mobsById[mob.optString("id")] = mob
        }
    }

    val rows = linkedMapOf<String, OptimisticGrazingRow>()
    val activeGrazing = json.optJSONArray("active_grazing") ?: JSONArray()
    for (index in 0 until activeGrazing.length()) {
        val session = activeGrazing.optJSONObject(index) ?: continue
        val mob = mobsById[session.optString("mob_id")] ?: continue
        val allocations = session.optJSONArray("allocations") ?: JSONArray()
        for (allocationIndex in 0 until allocations.length()) {
            val allocation = allocations.optJSONObject(allocationIndex) ?: continue
            val paddockId = allocation.optString("paddock_id")
            if (paddockId.isBlank()) {
                continue
            }
            val fraction = optimisticAllocationFraction(allocation, mob)
            val row = rows.getOrPut(paddockId) { OptimisticGrazingRow(paddockId) }
            row.mobs.add(
                JSONObject()
                    .put("mob_id", mob.optString("id"))
                    .put("mob_name", mob.optString("name", "Mob"))
                    .putOptional("start_at", session.optionalString("start_at"))
                    .put("allocation_fraction", fraction)
                    .put("allocation_pct", roundHead(fraction * 100.0)),
            )
            val explicitGroupCounts = allocation.optJSONArray("group_counts")
            if (explicitGroupCounts != null && explicitGroupCounts.length() > 0) {
                for (groupIndex in 0 until explicitGroupCounts.length()) {
                    val groupCount = explicitGroupCounts.optJSONObject(groupIndex) ?: continue
                    val groupId = groupCount.optString("animal_group_type_id")
                    if (groupId.isBlank()) continue
                    val balance = findBalance(mob, groupId)
                    val sourceHead = balance?.optDouble("head_count", 0.0) ?: 0.0
                    val groupFraction = if (groupCount.has("group_fraction") && !groupCount.isNull("group_fraction")) {
                        groupCount.optDouble("group_fraction")
                    } else {
                        Double.NaN
                    }
                    val head = if (!groupFraction.isNaN() && sourceHead > 0.0) {
                        sourceHead * groupFraction
                    } else {
                        groupCount.optDouble("head_count", 0.0)
                    }
                    val group = balance?.optJSONObject("animal_group_type")?.deepCopy()
                        ?: animalGroupTypeForId(json, groupId)
                        ?: JSONObject().put("id", groupId)
                    addOptimisticGroupHead(row, groupId, group, head)
                }
            } else {
                val balances = mob.optJSONArray("balances") ?: JSONArray()
                for (balanceIndex in 0 until balances.length()) {
                    val balance = balances.optJSONObject(balanceIndex) ?: continue
                    val head = balance.optDouble("head_count", 0.0) * fraction
                    if (head <= 0.0) {
                        continue
                    }
                    val group = balance.optJSONObject("animal_group_type") ?: JSONObject()
                    val groupId = balance.optString("animal_group_type_id", group.optString("id"))
                    addOptimisticGroupHead(row, groupId, group, head)
                }
            }
        }
    }

    val rebuilt = JSONArray()
    rows.values.forEach { row ->
        rebuilt.put(
            JSONObject()
                .put("paddock_id", row.paddockId)
                .put("total_head", roundHead(row.totalHead))
                .put(
                    "mobs",
                    JSONArray().apply {
                        row.mobs
                            .sortedBy { it.optString("mob_name").lowercase() }
                            .forEach(::put)
                    },
                )
                .put(
                    "species_heads",
                    JSONArray().apply {
                        row.speciesHeads.toSortedMap().forEach { (species, head) ->
                            put(JSONObject().put("species", species).put("head", roundHead(head)))
                        }
                    },
                )
                .put(
                    "group_heads",
                    JSONArray().apply {
                        row.groupHeads.values.forEach { group ->
                            put(
                                JSONObject()
                                    .put("animal_group_type_id", group.groupId)
                                    .put("animal_group_type", group.group.deepCopy())
                                    .put("head", roundHead(group.head)),
                            )
                        }
                    },
                ),
        )
    }
    json.put("active_grazing_by_paddock", rebuilt)
}

private fun addOrUpdateBalance(
    mob: JSONObject,
    groupId: String,
    group: JSONObject,
    quantity: Int,
    balanceId: String,
) {
    val balance = findBalance(mob, groupId)
    if (balance == null) {
        ensureArray(mob, "balances").put(
            JSONObject()
                .put("id", balanceId)
                .put("mob_id", mob.optString("id"))
                .put("animal_group_type_id", groupId)
                .put("animal_group_type", group.deepCopy().put("id", groupId))
                .put("head_count", quantity)
                .put("pending_sync", true),
        )
    } else {
        balance.put("head_count", quantity)
        balance.put("pending_sync", true)
    }
    mob.put("pending_sync", true)
}

private fun animalGroupTypeForId(json: JSONObject, groupId: String): JSONObject? {
    val mobs = json.optJSONArray("mobs") ?: return null
    for (mobIndex in 0 until mobs.length()) {
        val balances = mobs.optJSONObject(mobIndex)?.optJSONArray("balances") ?: continue
        for (balanceIndex in 0 until balances.length()) {
            val balance = balances.optJSONObject(balanceIndex) ?: continue
            if (balance.optString("animal_group_type_id") == groupId) {
                return balance.optJSONObject("animal_group_type")?.deepCopy()?.put("id", groupId)
            }
        }
    }
    return null
}

private fun findBalance(mob: JSONObject, groupId: String): JSONObject? {
    val balances = mob.optJSONArray("balances") ?: return null
    for (index in 0 until balances.length()) {
        val balance = balances.optJSONObject(index) ?: continue
        if (balance.optString("animal_group_type_id") == groupId) {
            return balance
        }
    }
    return null
}

private fun entityName(json: JSONObject, entityType: String, entityId: String): String? {
    val arrayName = when (entityType) {
        "paddock" -> "paddocks"
        "mob" -> "mobs"
        "water_asset" -> "water_assets"
        "fence_section" -> "fence_sections"
        else -> return null
    }
    return findObjectById(json.optJSONArray(arrayName), entityId)?.optString("name")?.takeIf { it.isNotBlank() }
}

private fun findObjectById(array: JSONArray?, id: String): JSONObject? {
    if (array == null || id.isBlank()) {
        return null
    }
    for (index in 0 until array.length()) {
        val item = array.optJSONObject(index) ?: continue
        if (item.optString("id") == id) {
            return item
        }
    }
    return null
}

private fun upsertObjectById(json: JSONObject, arrayName: String, item: JSONObject) {
    val array = ensureArray(json, arrayName)
    upsertObjectById(array, item)
}

private fun upsertObjectById(array: JSONArray, item: JSONObject) {
    val itemId = item.optString("id")
    for (index in 0 until array.length()) {
        if (array.optJSONObject(index)?.optString("id") == itemId) {
            array.put(index, item)
            return
        }
    }
    array.put(item)
}

private fun prependObject(json: JSONObject, arrayName: String, item: JSONObject) {
    val existing = ensureArray(json, arrayName)
    val next = JSONArray().put(item)
    for (index in 0 until existing.length()) {
        next.put(existing.get(index))
    }
    json.put(arrayName, next)
}

private fun ensureArray(json: JSONObject, name: String): JSONArray {
    val existing = json.optJSONArray(name)
    if (existing != null) {
        return existing
    }
    val created = JSONArray()
    json.put(name, created)
    return created
}

private fun copyArray(array: JSONArray?): JSONArray =
    if (array == null) JSONArray() else JSONArray(array.toString())

private fun JSONObject.deepCopy(): JSONObject = JSONObject(toString())

private fun JSONObject.optionalString(name: String): String? =
    if (isNull(name)) null else optString(name).takeIf { it.isNotBlank() }

private fun JSONObject.optionalDouble(name: String): Double? =
    if (!has(name) || isNull(name)) null else optDouble(name)

private fun JSONObject.putOptional(name: String, value: String?): JSONObject =
    if (value == null) put(name, JSONObject.NULL) else put(name, value)

private fun JSONObject.putOptional(name: String, value: Double?): JSONObject =
    if (value == null) put(name, JSONObject.NULL) else put(name, value)

private fun pendingId(command: JSONObject, prefix: String): String =
    "pending-$prefix-${command.optString("client_command_id")}"

private fun pendingPhotoId(photo: JSONObject): String =
    "pending-attachment-${photo.optString("client_attachment_id")}"

private fun taskStatusLabel(status: String): String =
    when (status) {
        "todo" -> "To Do"
        "selected_for_execution" -> "Selected For Execution"
        "in_progress" -> "In Progress"
        "impeded" -> "Impeded"
        "ready_for_verification" -> "Ready For Verification"
        "verification_in_progress" -> "Verification In Progress"
        "closed" -> "Closed"
        else -> labelFromValue(status)
    }

private fun taskPriorityLabel(priority: String): String =
    when (priority) {
        "lowest" -> "Lowest"
        "low" -> "Low"
        "high" -> "High"
        "highest" -> "Highest"
        else -> labelFromValue(priority)
    }

private fun labelFromValue(value: String): String =
    value.replace("_", " ").replaceFirstChar(Char::titlecase)

private fun roundHead(value: Double): Double = round(value * 100.0) / 100.0

private fun addOptimisticGroupHead(
    row: OptimisticGrazingRow,
    groupId: String,
    group: JSONObject,
    head: Double,
) {
    if (head <= 0.0) return
    val species = group.optString("species", "Stock").ifBlank { "Stock" }
    row.speciesHeads[species] = (row.speciesHeads[species] ?: 0.0) + head
    val groupHead = row.groupHeads.getOrPut(groupId) {
        OptimisticGroupHead(groupId, group.deepCopy(), 0.0)
    }
    groupHead.head += head
    row.totalHead += head
}

private fun optimisticAllocationFraction(allocation: JSONObject, mob: JSONObject): Double {
    if (allocation.has("allocation_fraction") && !allocation.isNull("allocation_fraction")) {
        return allocation.optDouble("allocation_fraction", 1.0)
    }
    val groupCounts = allocation.optJSONArray("group_counts") ?: return 1.0
    val totalLsu = optimisticMobTotalLsu(mob)
    if (totalLsu <= 0.0) return 1.0
    var assignedLsu = 0.0
    for (index in 0 until groupCounts.length()) {
        val groupCount = groupCounts.optJSONObject(index) ?: continue
        val groupId = groupCount.optString("animal_group_type_id")
        val balance = findBalance(mob, groupId)
        val group = balance?.optJSONObject("animal_group_type") ?: JSONObject().put("id", groupId)
        val sourceHead = balance?.optDouble("head_count", 0.0) ?: 0.0
        val groupFraction = if (groupCount.has("group_fraction") && !groupCount.isNull("group_fraction")) {
            groupCount.optDouble("group_fraction")
        } else {
            Double.NaN
        }
        val head = if (!groupFraction.isNaN() && sourceHead > 0.0) {
            sourceHead * groupFraction
        } else {
            groupCount.optDouble("head_count", 0.0)
        }
        assignedLsu += head * optimisticLsuPerHead(group)
    }
    return assignedLsu / totalLsu
}

private fun optimisticMobTotalLsu(mob: JSONObject): Double {
    var total = 0.0
    val balances = mob.optJSONArray("balances") ?: JSONArray()
    for (index in 0 until balances.length()) {
        val balance = balances.optJSONObject(index) ?: continue
        val group = balance.optJSONObject("animal_group_type") ?: JSONObject()
        total += balance.optDouble("head_count", 0.0) * optimisticLsuPerHead(group)
    }
    return total
}

private fun optimisticLsuPerHead(group: JSONObject): Double {
    val speciesKey = group.optString("species").trim().lowercase()
    val sexKey = group.optString("sex").trim().lowercase()
    val ageKey = group.optString("age_class").trim().lowercase()
    val base = when (speciesKey) {
        "cattle" -> 1.0
        "sheep" -> 1.0 / 6.0
        "goat" -> 1.0 / 8.0
        else -> 1.0
    }
    val juvenileLabels = when (speciesKey) {
        "cattle" -> setOf("calf")
        "sheep" -> setOf("lamb")
        "goat" -> setOf("kid")
        else -> emptySet()
    }
    val ageMultiplier = when {
        ageKey in juvenileLabels -> 0.3
        ageKey == "young" -> 0.8
        else -> 1.0
    }
    val sexMultiplier = when {
        sexKey in setOf("ram", "bul", "bull") && ageKey in setOf("adult", "old") -> 1.75
        sexKey in setOf("ram", "bul", "bull") && ageKey == "young" -> 1.2
        else -> 1.0
    }
    return base * ageMultiplier * sexMultiplier
}

private data class OptimisticGrazingRow(
    val paddockId: String,
    val mobs: MutableList<JSONObject> = mutableListOf(),
    val speciesHeads: MutableMap<String, Double> = linkedMapOf(),
    val groupHeads: MutableMap<String, OptimisticGroupHead> = linkedMapOf(),
    var totalHead: Double = 0.0,
)

private data class OptimisticGroupHead(
    val groupId: String,
    val group: JSONObject,
    var head: Double,
)

private data class OptimisticBalePrice(
    val pricePerKg: Double?,
    val totalPrice: Double?,
    val mode: String,
)
