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
        .put("allocations", copyArray(allocations))
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
    addLinks("paddock", payload.optJSONArray("paddock_ids"), ::addLink)
    addLinks("mob", payload.optJSONArray("mob_ids"), ::addLink)
    addLinks("water_asset", payload.optJSONArray("water_asset_ids"), ::addLink)
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
            val fraction = allocation.optDouble("allocation_fraction", 1.0)
            val row = rows.getOrPut(paddockId) { OptimisticGrazingRow(paddockId) }
            row.mobs.add(
                JSONObject()
                    .put("mob_id", mob.optString("id"))
                    .put("mob_name", mob.optString("name", "Mob"))
                    .putOptional("start_at", session.optionalString("start_at"))
                    .put("allocation_fraction", fraction)
                    .put("allocation_pct", roundHead(fraction * 100.0)),
            )
            val balances = mob.optJSONArray("balances") ?: JSONArray()
            for (balanceIndex in 0 until balances.length()) {
                val balance = balances.optJSONObject(balanceIndex) ?: continue
                val head = balance.optDouble("head_count", 0.0) * fraction
                if (head <= 0.0) {
                    continue
                }
                val group = balance.optJSONObject("animal_group_type") ?: JSONObject()
                val species = group.optString("species", "Stock").ifBlank { "Stock" }
                row.speciesHeads[species] = (row.speciesHeads[species] ?: 0.0) + head
                val groupId = balance.optString("animal_group_type_id", group.optString("id"))
                val groupHead = row.groupHeads.getOrPut(groupId) {
                    OptimisticGroupHead(groupId, group.deepCopy(), 0.0)
                }
                groupHead.head += head
                row.totalHead += head
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

private fun JSONObject.putOptional(name: String, value: String?): JSONObject =
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
