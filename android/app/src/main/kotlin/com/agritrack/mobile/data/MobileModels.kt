package com.agritrack.mobile.data

import org.json.JSONArray
import org.json.JSONObject

data class FarmSummary(
    val id: String,
    val name: String,
    val timezone: String,
    val role: String? = null,
) {
    val roleLabel: String?
        get() = role?.takeIf { it.isNotBlank() }?.replace("_", " ")?.replaceFirstChar(Char::titlecase)

    val displayLabel: String
        get() = roleLabel?.let { "$name - $it" } ?: name

    fun toJson(): JSONObject {
        val json = JSONObject()
            .put("id", id)
            .put("name", name)
            .put("timezone", timezone)
        role?.takeIf { it.isNotBlank() }?.let { json.put("role", it) }
        return json
    }

    companion object {
        fun fromJson(json: JSONObject): FarmSummary =
            FarmSummary(
                id = json.getString("id"),
                name = json.optString("name", "Farm"),
                timezone = json.optString("timezone", "UTC"),
                role = json.optNullableString("role"),
            )
    }
}

data class PaddockSummary(
    val id: String,
    val name: String,
    val status: String,
    val notes: String?,
    val tags: List<String>,
    val areaHa: Double? = null,
    val grazeableAreaHa: Double? = null,
) {
    val tagLabel: String
        get() = tags.joinToString(", ")
}

data class AnimalGroupTypeSummary(
    val id: String,
    val species: String,
    val breed: String,
    val sex: String,
    val ageClass: String,
) {
    val label: String
        get() = listOf(species, breed, sex, ageClass)
            .filter { it.isNotBlank() }
            .joinToString(" ")
            .ifBlank { "Animal group" }

    val lsuPerHead: Double
        get() {
            val speciesKey = species.trim().lowercase()
            val sexKey = sex.trim().lowercase()
            val ageKey = ageClass.trim().lowercase()
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
}

data class MobBalanceSummary(
    val id: String,
    val animalGroupTypeId: String,
    val animalGroupType: AnimalGroupTypeSummary,
    val headCount: Int,
)

data class MobSummary(
    val id: String,
    val name: String,
    val status: String,
    val balances: List<MobBalanceSummary>,
) {
    val totalHead: Int
        get() = balances.sumOf { it.headCount }

    val totalLsu: Double
        get() = balances.sumOf { balance ->
            balance.headCount * balance.animalGroupType.lsuPerHead
        }
}

data class GrazingAllocationSummary(
    val paddockId: String,
    val allocationFraction: Double,
)

data class ActiveGrazingSummary(
    val id: String,
    val mobId: String,
    val startAt: String?,
    val allocations: List<GrazingAllocationSummary>,
)

data class PaddockGrazingSummary(
    val paddockId: String,
    val totalHead: Double,
    val mobs: List<PaddockMobSummary>,
    val speciesHeads: List<SpeciesHeadSummary>,
    val groupHeads: List<PaddockGroupHeadSummary>,
)

data class PaddockMobSummary(
    val mobId: String,
    val mobName: String,
    val allocationPct: Double,
    val startAt: String? = null,
)

data class SpeciesHeadSummary(
    val species: String,
    val head: Double,
)

data class PaddockGroupHeadSummary(
    val animalGroupTypeId: String,
    val animalGroupType: AnimalGroupTypeSummary,
    val head: Double,
)

data class WaterAssetSummary(
    val id: String,
    val name: String,
    val assetType: String,
    val assetTypeLabel: String,
    val active: Boolean,
    val status: String?,
    val waterLevel: String?,
    val locationPaddockId: String?,
    val locationPaddockName: String?,
    val servedPaddockIds: List<String>,
    val networkWarning: String?,
)

data class RainfallSummary(
    val id: String,
    val recordedOn: String,
    val mm: Double,
    val note: String?,
)

data class MobEventSummary(
    val id: String,
    val mobId: String,
    val eventAt: String?,
    val tags: List<String>,
    val description: String,
)

data class PaddockEventSummary(
    val id: String,
    val paddockId: String,
    val eventAt: String?,
    val tags: List<String>,
    val description: String,
)

data class TaskEntityLinkSummary(
    val id: String,
    val taskId: String,
    val entityType: String,
    val entityId: String,
    val entityName: String?,
)

data class TaskAttachmentSummary(
    val id: String,
    val clientAttachmentId: String,
    val originalFilename: String,
    val contentType: String,
    val byteSize: Int,
    val caption: String?,
    val capturedAt: String?,
)

data class TaskCommentSummary(
    val id: String,
    val authorName: String?,
    val body: String,
    val createdAt: String?,
)

data class TaskSummary(
    val id: String,
    val displayKey: String,
    val heading: String,
    val description: String,
    val tags: List<String>,
    val assigneeName: String?,
    val status: String,
    val statusLabel: String,
    val priority: String,
    val priorityLabel: String,
    val dueDate: String?,
    val entityLinks: List<TaskEntityLinkSummary>,
    val commentCount: Int,
    val comments: List<TaskCommentSummary>,
    val attachmentCount: Int,
    val attachments: List<TaskAttachmentSummary>,
) {
    fun isLinkedTo(entityType: String, entityId: String): Boolean =
        entityLinks.any { it.entityType == entityType && it.entityId == entityId }
}

data class CalendarItemSummary(
    val kind: String,
    val date: String,
    val sourceId: String?,
    val taskId: String?,
    val activityId: String?,
    val title: String,
    val description: String?,
    val badgeText: String?,
    val subtitle: String?,
    val durationText: String?,
    val stage: String?,
    val stageLabel: String?,
    val assigneeName: String?,
    val tags: List<String>,
    val priority: String?,
    val priorityLabel: String?,
    val entityLinks: List<TaskEntityLinkSummary>,
    val recurrenceText: String?,
)

data class DecisionItemSummary(
    val severity: String,
    val category: String,
    val title: String,
    val detail: String,
    val entityType: String?,
    val entityId: String?,
    val taskId: String? = null,
    val activityId: String? = null,
    val farmId: String? = null,
    val farmName: String? = null,
)

data class MapFeatureSummary(
    val featureType: String,
    val name: String,
    val farmId: String?,
    val farmName: String?,
    val paddockId: String?,
    val waterAssetId: String?,
    val waterAlertLevel: String?,
    val waterAlertMessage: String?,
    val grazingPressureRatio: Double?,
    val currentLsu: Double?,
    val hectaresPerCurrentLsu: Double?,
    val mobs: List<PaddockMobSummary>,
    val geometryType: String,
    val geometryJson: String,
    val propertiesJson: String,
)

data class FarmSnapshot(
    val farm: FarmSummary,
    val paddockCount: Int,
    val mobCount: Int,
    val waterAssetCount: Int,
    val rainfallCount: Int,
    val mobEventCount: Int,
    val paddockEventCount: Int,
    val taskCount: Int,
    val calendarItemCount: Int,
    val decisionCount: Int,
    val paddocks: List<PaddockSummary>,
    val mobs: List<MobSummary>,
    val activeGrazing: List<ActiveGrazingSummary>,
    val grazingByPaddock: List<PaddockGrazingSummary>,
    val waterAssets: List<WaterAssetSummary>,
    val rainfall: List<RainfallSummary>,
    val mobEvents: List<MobEventSummary>,
    val paddockEvents: List<PaddockEventSummary>,
    val tasks: List<TaskSummary>,
    val calendarItems: List<CalendarItemSummary>,
    val decisionFeed: List<DecisionItemSummary>,
    val mapFeatures: List<MapFeatureSummary>,
    val mapWarnings: List<String>,
    val rawJson: String,
) {
    companion object {
        fun fromJson(json: JSONObject): FarmSnapshot {
            val farmJson = json.getJSONObject("farm")
            val paddocks = parsePaddocks(json.optJSONArray("paddocks") ?: JSONArray())
            val mobs = parseMobs(json.optJSONArray("mobs") ?: JSONArray())
            val activeGrazing = parseActiveGrazing(json.optJSONArray("active_grazing") ?: JSONArray())
            val grazingByPaddock = parseGrazingByPaddock(json.optJSONArray("active_grazing_by_paddock") ?: JSONArray())
            val waterAssets = parseWaterAssets(json.optJSONArray("water_assets") ?: JSONArray())
            val rainfall = parseRainfall(json.optJSONArray("rainfall") ?: JSONArray())
            val mobEvents = parseMobEvents(json.optJSONArray("mob_events") ?: JSONArray())
            val paddockEvents = parsePaddockEvents(json.optJSONArray("paddock_events") ?: JSONArray())
            val tasks = parseTasks(json.optJSONArray("tasks") ?: JSONArray())
            val calendarItems = parseCalendarItems(json.optJSONArray("calendar_items") ?: JSONArray())
            val decisions = parseDecisionFeed(json.optJSONArray("decision_feed") ?: JSONArray())
            val mapFeatures = parseMapFeatures(json.optJSONArray("map_features") ?: JSONArray())
            return FarmSnapshot(
                farm = FarmSummary(
                    id = farmJson.getString("id"),
                    name = farmJson.optString("name", "Farm"),
                    timezone = farmJson.optString("timezone", "UTC"),
                    role = farmJson.optNullableString("role"),
                ),
                paddockCount = paddocks.size,
                mobCount = mobs.size,
                waterAssetCount = waterAssets.size,
                rainfallCount = rainfall.size,
                mobEventCount = mobEvents.size,
                paddockEventCount = paddockEvents.size,
                taskCount = tasks.size,
                calendarItemCount = calendarItems.size,
                decisionCount = decisions.size,
                paddocks = paddocks,
                mobs = mobs,
                activeGrazing = activeGrazing,
                grazingByPaddock = grazingByPaddock,
                waterAssets = waterAssets,
                rainfall = rainfall,
                mobEvents = mobEvents,
                paddockEvents = paddockEvents,
                tasks = tasks,
                calendarItems = calendarItems,
                decisionFeed = decisions,
                mapFeatures = mapFeatures,
                mapWarnings = json.optJSONArray("map_warnings")?.strings().orEmpty(),
                rawJson = json.toString(),
            )
        }

        private fun parsePaddocks(json: JSONArray): List<PaddockSummary> = buildList {
            for (index in 0 until json.length()) {
                val paddock = json.getJSONObject(index)
                add(
                    PaddockSummary(
                        id = paddock.getString("id"),
                        name = paddock.optString("name", "Unnamed paddock"),
                        status = paddock.optString("status", "active"),
                        notes = paddock.optNullableString("notes"),
                        tags = paddock.optJSONArray("tags").strings(),
                        areaHa = paddock.optNullableDouble("area_ha"),
                        grazeableAreaHa = paddock.optNullableDouble("grazeable_area_ha"),
                    )
                )
            }
        }

        private fun parseMobs(json: JSONArray): List<MobSummary> = buildList {
            for (index in 0 until json.length()) {
                val mob = json.getJSONObject(index)
                add(
                    MobSummary(
                        id = mob.getString("id"),
                        name = mob.optString("name", "Unnamed mob"),
                        status = mob.optString("status", "unknown"),
                        balances = parseBalances(mob.optJSONArray("balances") ?: JSONArray()),
                    )
                )
            }
        }

        private fun parseBalances(json: JSONArray): List<MobBalanceSummary> = buildList {
            for (index in 0 until json.length()) {
                val balance = json.getJSONObject(index)
                val groupJson = balance.optJSONObject("animal_group_type") ?: JSONObject()
                val groupId = balance.optString("animal_group_type_id", groupJson.optString("id"))
                add(
                    MobBalanceSummary(
                        id = balance.optString("id"),
                        animalGroupTypeId = groupId,
                        animalGroupType = parseAnimalGroupType(groupJson, groupId),
                        headCount = balance.optInt("head_count", 0),
                    )
                )
            }
        }

        private fun parseActiveGrazing(json: JSONArray): List<ActiveGrazingSummary> = buildList {
            for (index in 0 until json.length()) {
                val item = json.getJSONObject(index)
                val allocationsJson = item.optJSONArray("allocations") ?: JSONArray()
                val allocations = buildList {
                    for (allocationIndex in 0 until allocationsJson.length()) {
                        val allocation = allocationsJson.getJSONObject(allocationIndex)
                        add(
                            GrazingAllocationSummary(
                                paddockId = allocation.optString("paddock_id"),
                                allocationFraction = allocation.optDouble("allocation_fraction", 1.0),
                            )
                        )
                    }
                }
                add(
                    ActiveGrazingSummary(
                        id = item.optString("id"),
                        mobId = item.optString("mob_id"),
                        startAt = item.optNullableString("start_at"),
                        allocations = allocations,
                    )
                )
            }
        }

        private fun parseGrazingByPaddock(json: JSONArray): List<PaddockGrazingSummary> = buildList {
            for (index in 0 until json.length()) {
                val item = json.getJSONObject(index)
                val mobsJson = item.optJSONArray("mobs") ?: JSONArray()
                val speciesJson = item.optJSONArray("species_heads") ?: JSONArray()
                val groupHeadsJson = item.optJSONArray("group_heads") ?: JSONArray()
                add(
                    PaddockGrazingSummary(
                        paddockId = item.optString("paddock_id"),
                        totalHead = item.optDouble("total_head", 0.0),
                        mobs = buildList {
                            for (mobIndex in 0 until mobsJson.length()) {
                                val mob = mobsJson.getJSONObject(mobIndex)
                                add(
                                    PaddockMobSummary(
                                        mobId = mob.optString("mob_id"),
                                        mobName = mob.optString("mob_name", "Mob"),
                                        allocationPct = mob.optDouble("allocation_pct", 100.0),
                                        startAt = mob.optNullableString("start_at"),
                                    )
                                )
                            }
                        },
                        groupHeads = buildList {
                            for (groupIndex in 0 until groupHeadsJson.length()) {
                                val groupHead = groupHeadsJson.getJSONObject(groupIndex)
                                val groupJson = groupHead.optJSONObject("animal_group_type") ?: JSONObject()
                                val groupId = groupHead.optString("animal_group_type_id", groupJson.optString("id"))
                                add(
                                    PaddockGroupHeadSummary(
                                        animalGroupTypeId = groupId,
                                        animalGroupType = parseAnimalGroupType(groupJson, groupId),
                                        head = groupHead.optDouble("head", 0.0),
                                    )
                                )
                            }
                        },
                        speciesHeads = buildList {
                            for (speciesIndex in 0 until speciesJson.length()) {
                                val species = speciesJson.getJSONObject(speciesIndex)
                                add(
                                    SpeciesHeadSummary(
                                        species = species.optString("species", "Stock"),
                                        head = species.optDouble("head", 0.0),
                                    )
                                )
                            }
                        },
                    )
                )
            }
        }

        private fun parseWaterAssets(json: JSONArray): List<WaterAssetSummary> = buildList {
            for (index in 0 until json.length()) {
                val asset = json.getJSONObject(index)
                add(
                    WaterAssetSummary(
                        id = asset.getString("id"),
                        name = asset.optString("name", "Water asset"),
                        assetType = asset.optString("asset_type"),
                        assetTypeLabel = asset.optString("asset_type_label", asset.optString("asset_type")),
                        active = asset.optBoolean("active", true),
                        status = asset.optNullableString("status"),
                        waterLevel = asset.optNullableString("water_level"),
                        locationPaddockId = asset.optNullableString("location_paddock_id"),
                        locationPaddockName = asset.optNullableString("location_paddock_name"),
                        servedPaddockIds = asset.optJSONArray("served_paddock_ids").strings(),
                        networkWarning = asset.optNullableString("network_warning"),
                    )
                )
            }
        }

        private fun parseRainfall(json: JSONArray): List<RainfallSummary> = buildList {
            for (index in 0 until json.length()) {
                val rain = json.getJSONObject(index)
                add(
                    RainfallSummary(
                        id = rain.optString("id"),
                        recordedOn = rain.optString("recorded_on"),
                        mm = rain.optDouble("mm", 0.0),
                        note = rain.optNullableString("note"),
                    )
                )
            }
        }

        private fun parseMobEvents(json: JSONArray): List<MobEventSummary> = buildList {
            for (index in 0 until json.length()) {
                val event = json.getJSONObject(index)
                add(
                    MobEventSummary(
                        id = event.optString("id"),
                        mobId = event.optString("mob_id"),
                        eventAt = event.optNullableString("event_at"),
                        tags = event.optJSONArray("tags").strings(),
                        description = event.optString("description"),
                    )
                )
            }
        }

        private fun parsePaddockEvents(json: JSONArray): List<PaddockEventSummary> = buildList {
            for (index in 0 until json.length()) {
                val event = json.getJSONObject(index)
                add(
                    PaddockEventSummary(
                        id = event.optString("id"),
                        paddockId = event.optString("paddock_id"),
                        eventAt = event.optNullableString("event_at"),
                        tags = event.optJSONArray("tags").strings(),
                        description = event.optString("description"),
                    )
                )
            }
        }

        private fun parseTasks(json: JSONArray): List<TaskSummary> = buildList {
            for (index in 0 until json.length()) {
                val task = json.getJSONObject(index)
                val linksJson = task.optJSONArray("entity_links") ?: JSONArray()
                val commentsJson = task.optJSONArray("comments") ?: JSONArray()
                val attachmentsJson = task.optJSONArray("attachments") ?: JSONArray()
                add(
                    TaskSummary(
                        id = task.optString("id"),
                        displayKey = task.optString("display_key", "TASK"),
                        heading = task.optString("heading", "Task"),
                        description = task.optString("description"),
                        tags = task.optJSONArray("tags").strings(),
                        assigneeName = task.optNullableString("assignee_name"),
                        status = task.optString("status", "todo"),
                        statusLabel = task.optString("status_label", task.optString("status", "Task")),
                        priority = task.optString("priority", "low"),
                        priorityLabel = task.optString("priority_label", task.optString("priority", "Low")),
                        dueDate = task.optNullableString("due_date"),
                        entityLinks = parseTaskEntityLinks(linksJson),
                        commentCount = task.optInt("comment_count", commentsJson.length()),
                        comments = parseTaskComments(commentsJson),
                        attachmentCount = task.optInt("attachment_count", attachmentsJson.length()),
                        attachments = buildList {
                            for (attachmentIndex in 0 until attachmentsJson.length()) {
                                val attachment = attachmentsJson.getJSONObject(attachmentIndex)
                                add(
                                    TaskAttachmentSummary(
                                        id = attachment.optString("id"),
                                        clientAttachmentId = attachment.optString("client_attachment_id"),
                                        originalFilename = attachment.optString("original_filename", "photo"),
                                        contentType = attachment.optString("content_type", "image/jpeg"),
                                        byteSize = attachment.optInt("byte_size", 0),
                                        caption = attachment.optNullableString("caption"),
                                        capturedAt = attachment.optNullableString("captured_at"),
                                    )
                                )
                            }
                        },
                    )
                )
            }
        }

        private fun parseTaskComments(json: JSONArray): List<TaskCommentSummary> = buildList {
            for (commentIndex in 0 until json.length()) {
                val comment = json.getJSONObject(commentIndex)
                val body = comment.optString("body")
                if (body.isNotBlank()) {
                    add(
                        TaskCommentSummary(
                            id = comment.optString("id"),
                            authorName = comment.optNullableString("author_name"),
                            body = body,
                            createdAt = comment.optNullableString("created_at"),
                        )
                    )
                }
            }
        }

        private fun parseCalendarItems(json: JSONArray): List<CalendarItemSummary> = buildList {
            for (index in 0 until json.length()) {
                val item = json.getJSONObject(index)
                add(
                    CalendarItemSummary(
                        kind = item.optString("kind"),
                        date = item.optString("date"),
                        sourceId = item.optNullableString("source_id"),
                        taskId = item.optNullableString("task_id"),
                        activityId = item.optNullableString("activity_id"),
                        title = item.optString("title", "Calendar item"),
                        description = item.optNullableString("description"),
                        badgeText = item.optNullableString("badge_text"),
                        subtitle = item.optNullableString("subtitle"),
                        durationText = item.optNullableString("duration_text"),
                        stage = item.optNullableString("stage"),
                        stageLabel = item.optNullableString("stage_label"),
                        assigneeName = item.optNullableString("assignee_name"),
                        tags = item.optJSONArray("tags").strings(),
                        priority = item.optNullableString("priority"),
                        priorityLabel = item.optNullableString("priority_label"),
                        entityLinks = parseTaskEntityLinks(item.optJSONArray("entity_links") ?: JSONArray()),
                        recurrenceText = item.optNullableString("recurrence_text"),
                    )
                )
            }
        }

        private fun parseTaskEntityLinks(json: JSONArray): List<TaskEntityLinkSummary> = buildList {
            for (linkIndex in 0 until json.length()) {
                val link = json.getJSONObject(linkIndex)
                add(
                    TaskEntityLinkSummary(
                        id = link.optString("id"),
                        taskId = link.optString("task_id"),
                        entityType = link.optString("entity_type"),
                        entityId = link.optString("entity_id"),
                        entityName = link.optNullableString("entity_name"),
                    )
                )
            }
        }

        private fun parseDecisionFeed(json: JSONArray): List<DecisionItemSummary> = buildList {
            for (index in 0 until json.length()) {
                val item = json.getJSONObject(index)
                add(
                    DecisionItemSummary(
                        severity = item.optString("severity", "medium"),
                        category = item.optString("category", "field"),
                        title = item.optString("title", "Field decision"),
                        detail = item.optString("detail"),
                        entityType = item.optNullableString("entity_type"),
                        entityId = item.optNullableString("entity_id"),
                        taskId = item.optNullableString("task_id"),
                        activityId = item.optNullableString("activity_id"),
                        farmId = item.optNullableString("farm_id"),
                        farmName = item.optNullableString("farm_name"),
                    )
                )
            }
        }

        private fun parseMapFeatures(json: JSONArray): List<MapFeatureSummary> = buildList {
            for (index in 0 until json.length()) {
                val feature = json.getJSONObject(index)
                val properties = feature.optJSONObject("properties") ?: JSONObject()
                val geometry = feature.optJSONObject("geometry") ?: JSONObject()
                val mobsJson = properties.optJSONArray("mobs") ?: JSONArray()
                add(
                    MapFeatureSummary(
                        featureType = properties.optString("feature_type", "feature"),
                        name = properties.optString("name", "Map feature"),
                        farmId = properties.optNullableString("farm_id"),
                        farmName = properties.optNullableString("farm_name"),
                        paddockId = properties.optNullableString("paddock_id"),
                        waterAssetId = properties.optNullableString("id"),
                        waterAlertLevel = properties.optNullableString("water_alert_level"),
                        waterAlertMessage = properties.optNullableString("water_alert_message"),
                        grazingPressureRatio = properties.optNullableDouble("grazing_pressure_ratio"),
                        currentLsu = properties.optNullableDouble("current_lsu"),
                        hectaresPerCurrentLsu = properties.optNullableDouble("paddock_ha_per_current_lsu"),
                        mobs = buildList {
                            for (mobIndex in 0 until mobsJson.length()) {
                                val mob = mobsJson.getJSONObject(mobIndex)
                                add(
                                    PaddockMobSummary(
                                        mobId = mob.optString("mob_id"),
                                        mobName = mob.optString("mob_name", "Mob"),
                                        allocationPct = mob.optDouble("allocation_pct", 100.0),
                                        startAt = mob.optNullableString("start_at"),
                                    )
                                )
                            }
                        },
                        geometryType = geometry.optString("type"),
                        geometryJson = geometry.toString(),
                        propertiesJson = properties.toString(),
                    )
                )
            }
        }
    }
}

data class BootstrapResult(
    val farms: List<FarmSummary>,
    val animalGroupTypes: List<AnimalGroupTypeSummary>,
    val supportedCommandTypes: List<String>,
    val formOptions: MobileFormOptions = MobileFormOptions(),
) {
    companion object {
        fun fromJson(json: JSONObject): BootstrapResult {
            val farmsJson = json.optJSONArray("farms") ?: JSONArray()
            val farms = buildList {
                for (index in 0 until farmsJson.length()) {
                    val farm = farmsJson.getJSONObject(index)
                    add(FarmSummary.fromJson(farm))
                }
            }
            val groupTypesJson = json.optJSONArray("animal_group_types") ?: JSONArray()
            val groupTypes = buildList {
                for (index in 0 until groupTypesJson.length()) {
                    add(parseAnimalGroupType(groupTypesJson.getJSONObject(index)))
                }
            }
            val supported = json
                .optJSONObject("sync")
                ?.optJSONArray("supported_command_types")
                ?: JSONArray()
            return BootstrapResult(
                farms = farms,
                animalGroupTypes = groupTypes,
                supportedCommandTypes = supported.strings(),
                formOptions = MobileFormOptions.fromJson(json.optJSONObject("form_options") ?: JSONObject()),
            )
        }
    }
}

data class MobileOption(
    val value: String,
    val label: String,
)

data class MobileFormOptions(
    val speciesOptions: List<MobileOption> = emptyList(),
    val sexOptionsBySpecies: Map<String, List<MobileOption>> = emptyMap(),
    val ageClassOptionsBySpecies: Map<String, List<MobileOption>> = emptyMap(),
    val taskStatuses: List<MobileOption> = emptyList(),
    val taskPriorities: List<MobileOption> = emptyList(),
    val waterStatusOptionsByType: Map<String, List<MobileOption>> = emptyMap(),
    val waterLevelAssetTypes: Set<String> = emptySet(),
    val waterLevelOptions: List<MobileOption> = emptyList(),
) {
    companion object {
        fun fromJson(json: JSONObject): MobileFormOptions {
            return MobileFormOptions(
                speciesOptions = parseOptions(json.optJSONArray("species_options") ?: JSONArray()),
                sexOptionsBySpecies = parseOptionMap(json.optJSONObject("sex_options_by_species") ?: JSONObject()),
                ageClassOptionsBySpecies = parseOptionMap(json.optJSONObject("age_class_options_by_species") ?: JSONObject()),
                taskStatuses = parseOptions(json.optJSONArray("task_statuses") ?: JSONArray()),
                taskPriorities = parseOptions(json.optJSONArray("task_priorities") ?: JSONArray()),
                waterStatusOptionsByType = parseOptionMap(json.optJSONObject("water_status_options_by_type") ?: JSONObject()),
                waterLevelAssetTypes = json.optJSONArray("water_level_asset_types").strings().toSet(),
                waterLevelOptions = parseOptions(json.optJSONArray("water_level_options") ?: JSONArray()),
            )
        }

        private fun parseOptionMap(json: JSONObject): Map<String, List<MobileOption>> {
            val optionsByKey = linkedMapOf<String, List<MobileOption>>()
            val keys = json.keys()
            while (keys.hasNext()) {
                val key = keys.next()
                optionsByKey[key] = parseOptions(json.optJSONArray(key) ?: JSONArray())
            }
            return optionsByKey
        }

        private fun parseOptions(json: JSONArray): List<MobileOption> = buildList {
            for (index in 0 until json.length()) {
                val item = json.getJSONObject(index)
                val value = item.optString("value")
                if (value.isNotBlank()) {
                    add(MobileOption(value = value, label = item.optString("label", value)))
                }
            }
        }
    }
}

data class SyncResult(
    val clientCommandId: String,
    val type: String,
    val status: String,
    val duplicate: Boolean,
    val errorMessage: String?,
    val response: JSONObject?,
) {
    companion object {
        fun fromJson(json: JSONObject): SyncResult {
            val error = json.optJSONObject("error")
            return SyncResult(
                clientCommandId = json.optString("client_command_id"),
                type = json.optString("type"),
                status = json.optString("status"),
                duplicate = json.optBoolean("duplicate", false),
                errorMessage = error?.optString("message"),
                response = json.optJSONObject("response"),
            )
        }
    }
}

data class SyncSummary(
    val results: List<SyncResult>,
    val remainingQueueCount: Int,
    val refreshedSnapshot: FarmSnapshot? = null,
    val refreshedSnapshots: List<FarmSnapshot> = emptyList(),
) {
    val appliedCount: Int = results.count { it.status == "applied" }
    val failedCount: Int = results.count { it.status == "failed" }
}

data class FarmLoadResult(
    val bootstrap: BootstrapResult,
    val availableFarms: List<FarmSummary>,
    val activeFarm: FarmSummary?,
    val snapshot: FarmSnapshot?,
    val snapshots: List<FarmSnapshot> = snapshot?.let { listOf(it) }.orEmpty(),
    val prefetchedCount: Int = if (snapshot == null) 0 else 1,
    val failedFarmCount: Int = 0,
)

private fun parseAnimalGroupType(json: JSONObject, fallbackId: String = ""): AnimalGroupTypeSummary =
    AnimalGroupTypeSummary(
        id = json.optString("id", fallbackId),
        species = json.optString("species"),
        breed = json.optString("breed"),
        sex = json.optString("sex"),
        ageClass = json.optString("age_class"),
    )

private fun JSONArray?.strings(): List<String> {
    if (this == null) {
        return emptyList()
    }
    return buildList {
        for (index in 0 until length()) {
            val value = optString(index)
            if (value.isNotBlank()) {
                add(value)
            }
        }
    }
}

private fun JSONObject.optNullableString(name: String): String? =
    if (isNull(name)) null else optString(name).takeIf { it.isNotBlank() }

private fun JSONObject.optNullableDouble(name: String): Double? {
    if (isNull(name)) return null
    val value = optDouble(name, Double.NaN)
    return if (value.isNaN()) null else value
}
