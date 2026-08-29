package com.agritrack.mobile.data

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Test

class MobileModelsTest {
    @Test
    fun snapshotParserKeepsFarmCountsAndMobs() {
        val snapshot = FarmSnapshot.fromJson(
            JSONObject()
                .put(
                    "farm",
                    JSONObject()
                        .put("id", "farm-1")
                        .put("name", "North Block")
                        .put("timezone", "Africa/Johannesburg")
                )
                .put(
                    "paddocks",
                    JSONArray()
                        .put(
                            JSONObject()
                                .put("id", "paddock-1")
                                .put("name", "North Camp")
                                .put("area_ha", 12.5)
                                .put("grazeable_area_ha", 10.0)
                        )
                )
                .put(
                    "mobs",
                    JSONArray()
                        .put(
                            JSONObject()
                                .put("id", "mob-1")
                                .put("name", "Main Mob")
                                .put("status", "active")
                                .put(
                                    "balances",
                                    JSONArray()
                                        .put(
                                            JSONObject()
                                                .put("id", "balance-1")
                                                .put("animal_group_type_id", "group-1")
                                                .put("cohort_id", "cohort-1")
                                                .put("reproductive_state", "pregnant")
                                                .put("expected_litter_size", "twins")
                                                .put("lactation_state", "dry")
                                                .put("offspring_at_foot", "none")
                                                .put("head_count", 37)
                                                .put(
                                                    "animal_group_type",
                                                    JSONObject()
                                                        .put("id", "group-1")
                                                        .put("species", "Cattle")
                                                        .put("breed", "Bonsmara")
                                                        .put("sex", "cow")
                                                        .put("age_class", "adult")
                                                )
                                        )
                                )
                        )
                )
                .put("water_assets", JSONArray().put(JSONObject().put("id", "tank-1")))
                .put(
                    "gates",
                    JSONArray()
                        .put(
                            JSONObject()
                                .put("id", "gate-1")
                                .put("farm_id", "farm-1")
                                .put("paddock_a_id", "paddock-1")
                                .put("paddock_a_name", "North Camp")
                                .put("paddock_b_id", "paddock-2")
                                .put("paddock_b_name", "South Camp")
                                .put("name", "North Camp / South Camp Gate")
                                .put("status", "closed")
                                .put("active", true)
                                .put("source", "auto")
                                .put("latitude", -34.0)
                                .put("longitude", 18.0)
                        )
                )
                .put(
                    "active_grazing_by_paddock",
                    JSONArray()
                        .put(
                            JSONObject()
                                .put("paddock_id", "paddock-1")
                                .put("total_head", 37.0)
                                .put(
                                    "mobs",
                                    JSONArray()
                                        .put(
                                            JSONObject()
                                                .put("mob_id", "mob-1")
                                                .put("mob_name", "Main Mob")
                                                .put("allocation_pct", 100.0)
                                                .put("start_at", "2026-05-24T12:00:00+00:00")
                                        )
                                )
                                .put(
                                    "group_heads",
                                    JSONArray()
                                        .put(
                                            JSONObject()
                                                .put("animal_group_type_id", "group-1")
                                                .put("head", 37.0)
                                                .put(
                                                    "animal_group_type",
                                                    JSONObject()
                                                        .put("id", "group-1")
                                                        .put("species", "Cattle")
                                                        .put("breed", "Bonsmara")
                                                        .put("sex", "cow")
                                                        .put("age_class", "adult")
                                                )
                                        )
                                )
                        )
                )
                .put("rainfall", JSONArray().put(JSONObject().put("id", "rain-1")))
                .put(
                    "mob_events",
                    JSONArray()
                        .put(
                            JSONObject()
                                .put("id", "event-1")
                                .put("mob_id", "mob-1")
                                .put("description", "Mob looks settled")
                                .put("attachment_count", 1)
                                .put(
                                    "attachments",
                                    JSONArray()
                                        .put(
                                            JSONObject()
                                                .put("id", "note-attachment-1")
                                                .put("event_type", "mob_event")
                                                .put("event_id", "event-1")
                                                .put("client_attachment_id", "note-photo-1")
                                                .put("original_filename", "mob-note.jpg")
                                                .put("content_type", "image/jpeg")
                                                .put("byte_size", 21)
                                        )
                                )
                        )
                )
                .put(
                    "paddock_events",
                    JSONArray()
                        .put(
                            JSONObject()
                                .put("id", "paddock-event-1")
                                .put("paddock_id", "paddock-1")
                                .put("description", "Pasture recovering")
                        )
                )
                .put(
                    "water_asset_events",
                    JSONArray()
                        .put(
                            JSONObject()
                                .put("id", "water-event-1")
                                .put("water_asset_id", "tank-1")
                                .put("description", "Tank checked")
                        )
                )
                .put(
                    "fence_sections",
                    JSONArray()
                        .put(
                            JSONObject()
                                .put("id", "fence-1")
                                .put("farm_id", "farm-1")
                                .put("name", "North Boundary Fence")
                                .put("section_type", "boundary")
                                .put("section_type_label", "Boundary")
                                .put("paddock_a_id", "paddock-1")
                                .put("paddock_a_name", "North Camp")
                                .put("condition", "bad")
                                .put("condition_label", "Bad")
                                .put("height_profile", "low")
                                .put("height_profile_label", "Low")
                                .put("construction_type", "mesh")
                                .put("construction_type_label", "Mesh")
                                .put("length_m", 125.5)
                                .put("electric_wire", true)
                        )
                )
                .put(
                    "fence_events",
                    JSONArray()
                        .put(
                            JSONObject()
                                .put("id", "fence-event-1")
                                .put("fence_section_id", "fence-1")
                                .put("event_type", "maintenance")
                                .put("event_type_label", "Maintenance")
                                .put("condition_after", "fair")
                                .put("condition_after_label", "Fair")
                                .put("description", "Packed stones below the fence")
                                .put(
                                    "materials",
                                    JSONArray().put(
                                        JSONObject()
                                            .put("id", "material-1")
                                            .put("action", "packed")
                                            .put("action_label", "Packed")
                                            .put("material_type", "stone")
                                            .put("material_type_label", "Stone")
                                            .put("quantity", 2.0)
                                            .put("unit", "bag")
                                    )
                                )
                        )
                )
                .put(
                    "incidents",
                    JSONArray()
                        .put(
                            JSONObject()
                                .put("id", "incident-1")
                                .put("farm_id", "farm-1")
                                .put("occurred_on", "2026-05-23")
                                .put("category", "Stock missing")
                                .put("note", "Two ewes missing from North Camp")
                                .put("tags", JSONArray().put("stock").put("security"))
                                .put("reported_by", "Field Team")
                        )
                )
                .put(
                    "water_asset_state_history",
                    JSONArray()
                        .put(
                            JSONObject()
                                .put("id", "water-history-1")
                                .put("water_asset_id", "tank-1")
                                .put("change_type", "updated")
                                .put("changed_at", "2026-05-24T12:00:00+00:00")
                                .put("previous_active", true)
                                .put("previous_status", "operational")
                                .put("previous_water_level", "low")
                                .put("active", true)
                                .put("status", "operational")
                                .put("water_level", "full")
                        )
                )
                .put(
                    "tasks",
                    JSONArray()
                        .put(
                            JSONObject()
                                .put("id", "task-1")
                                .put("display_key", "OPS-1")
                                .put("heading", "Check water")
                                .put("tags", JSONArray().put("water").put("field"))
                                .put("assignee_name", "Field Team")
                                .put("status", "todo")
                                .put("status_label", "TO DO")
                                .put("priority_label", "High")
                                .put("attachment_count", 1)
                                .put(
                                    "attachments",
                                    JSONArray()
                                        .put(
                                            JSONObject()
                                                .put("id", "attachment-1")
                                                .put("client_attachment_id", "photo-1")
                                                .put("original_filename", "trough.jpg")
                                                .put("content_type", "image/jpeg")
                                                .put("byte_size", 12)
                                        )
                                )
                                .put(
                                    "entity_links",
                                    JSONArray()
                                        .put(
                                            JSONObject()
                                                .put("id", "link-1")
                                                .put("task_id", "task-1")
                                                .put("entity_type", "paddock")
                                                .put("entity_id", "paddock-1")
                                        )
                                )
                        )
                )
                .put(
                    "calendar_items",
                    JSONArray()
                        .put(
                            JSONObject()
                                .put("kind", "task")
                                .put("date", "2026-05-22")
                                .put("source_id", "task-1")
                                .put("task_id", "task-1")
                                .put("title", "Check water")
                                .put("description", "Confirm level")
                                .put("stage", "todo")
                                .put("stage_label", "TO DO")
                                .put("assignee_name", "Field Team")
                                .put("tags", JSONArray().put("water"))
                                .put(
                                    "entity_links",
                                    JSONArray()
                                        .put(
                                            JSONObject()
                                                .put("id", "link-1")
                                                .put("task_id", "task-1")
                                                .put("entity_type", "paddock")
                                                .put("entity_id", "paddock-1")
                                        )
                                )
                        )
                        .put(
                            JSONObject()
                                .put("kind", "incident")
                                .put("date", "2026-05-23")
                                .put("source_id", "incident-1")
                                .put("incident_id", "incident-1")
                                .put("title", "Stock missing")
                                .put("description", "Two ewes missing from North Camp")
                                .put("stage", "incident")
                                .put("stage_label", "Incident")
                                .put("tags", JSONArray().put("stock").put("security"))
                        )
                )
                .put(
                    "decision_feed",
                    JSONArray()
                        .put(
                            JSONObject()
                                .put("severity", "high")
                                .put("category", "water")
                                .put("title", "Water risk")
                                .put("detail", "North trough is empty")
                                .put("farm_id", "farm-1")
                                .put("farm_name", "North Block")
                        )
                )
                .put(
                    "map_features",
                    JSONArray()
                        .put(
                            JSONObject()
                                .put("type", "Feature")
                                .put(
                                    "geometry",
                                    JSONObject()
                                        .put("type", "Point")
                                        .put("coordinates", JSONArray().put(18.0).put(-34.0))
                                )
                                .put(
                                    "properties",
                                    JSONObject()
                                        .put("feature_type", "paddock")
                                        .put("name", "North Camp")
                                        .put("paddock_id", "paddock-1")
                                        .put("grazing_pressure_ratio", 0.42)
                                )
                        )
                        .put(
                            JSONObject()
                                .put("type", "Feature")
                                .put(
                                    "geometry",
                                    JSONObject()
                                        .put("type", "Point")
                                        .put("coordinates", JSONArray().put(18.001).put(-34.001))
                                )
                                .put(
                                    "properties",
                                    JSONObject()
                                        .put("feature_type", "gate")
                                        .put("gate_id", "gate-1")
                                        .put("name", "North Camp / South Camp Gate")
                                        .put("status", "closed")
                                )
                        )
                        .put(
                            JSONObject()
                                .put("type", "Feature")
                                .put(
                                    "geometry",
                                    JSONObject()
                                        .put("type", "LineString")
                                        .put(
                                            "coordinates",
                                            JSONArray()
                                                .put(JSONArray().put(18.0).put(-34.0))
                                                .put(JSONArray().put(18.002).put(-34.001))
                                        )
                                )
                                .put(
                                    "properties",
                                    JSONObject()
                                        .put("feature_type", "fence_section")
                                        .put("fence_section_id", "fence-1")
                                        .put("name", "North Boundary Fence")
                                        .put("condition", "bad")
                                )
                        )
                )
        )

        assertEquals("North Block", snapshot.farm.name)
        assertEquals(1, snapshot.paddockCount)
        assertEquals(1, snapshot.mobCount)
        assertEquals(1, snapshot.waterAssetCount)
        assertEquals("closed", snapshot.gates.first().status)
        assertEquals(1, snapshot.mobEventCount)
        assertEquals(1, snapshot.paddockEventCount)
        assertEquals(1, snapshot.waterAssetEventCount)
        assertEquals("North Camp", snapshot.paddocks.first().name)
        assertEquals(12.5, snapshot.paddocks.first().areaHa ?: 0.0, 0.0)
        assertEquals(10.0, snapshot.paddocks.first().grazeableAreaHa ?: 0.0, 0.0)
        assertEquals("Main Mob", snapshot.mobs.first().name)
        assertEquals(37, snapshot.mobs.first().balances.first().headCount)
        assertEquals(37.0, snapshot.mobs.first().totalLsu, 0.0)
        assertEquals("Cattle Bonsmara cow adult", snapshot.mobs.first().balances.first().animalGroupType.label)
        assertEquals("cohort-1", snapshot.mobs.first().balances.first().cohortId)
        assertEquals("pregnant", snapshot.mobs.first().balances.first().reproductiveState)
        assertEquals("dry", snapshot.mobs.first().balances.first().lactationState)
        assertEquals(
            "Cattle Bonsmara cow adult | pregnant | expected twins | dry | offspring at foot: none",
            snapshot.mobs.first().balances.first().displayLabel,
        )
        assertEquals("Cattle Bonsmara cow adult", snapshot.grazingByPaddock.first().groupHeads.first().animalGroupType.label)
        assertEquals(37.0, snapshot.grazingByPaddock.first().groupHeads.first().head, 0.0)
        assertEquals("2026-05-24T12:00:00+00:00", snapshot.grazingByPaddock.first().mobs.first().startAt)
        assertEquals("Mob looks settled", snapshot.mobEvents.first().description)
        assertEquals(1, snapshot.mobEvents.first().attachmentCount)
        assertEquals("mob-note.jpg", snapshot.mobEvents.first().attachments.first().originalFilename)
        assertEquals("Pasture recovering", snapshot.paddockEvents.first().description)
        assertEquals("Tank checked", snapshot.waterAssetEvents.first().description)
        assertEquals(1, snapshot.fenceSectionCount)
        assertEquals("North Boundary Fence", snapshot.fenceSections.first().name)
        assertEquals(true, snapshot.fenceSections.first().electricWire)
        assertEquals(1, snapshot.fenceEventCount)
        assertEquals("Packed stones below the fence", snapshot.fenceEvents.first().description)
        assertEquals("stone", snapshot.fenceEvents.first().materials.first().materialType)
        assertEquals(1, snapshot.incidentCount)
        assertEquals("Stock missing", snapshot.incidents.first().category)
        assertEquals(listOf("stock", "security"), snapshot.incidents.first().tags)
        assertEquals(1, snapshot.waterAssetStateHistoryCount)
        assertEquals("low", snapshot.waterAssetStateHistory.first().previousWaterLevel)
        assertEquals("full", snapshot.waterAssetStateHistory.first().waterLevel)
        assertEquals("OPS-1", snapshot.tasks.first().displayKey)
        assertEquals(listOf("water", "field"), snapshot.tasks.first().tags)
        assertEquals("Field Team", snapshot.tasks.first().assigneeName)
        assertEquals("High", snapshot.tasks.first().priorityLabel)
        assertEquals(true, snapshot.tasks.first().isLinkedTo("paddock", "paddock-1"))
        assertEquals(1, snapshot.tasks.first().attachmentCount)
        assertEquals("trough.jpg", snapshot.tasks.first().attachments.first().originalFilename)
        assertEquals(0.42, snapshot.mapFeatures.first().grazingPressureRatio ?: 0.0, 0.0)
        assertEquals("gate-1", snapshot.mapFeatures[1].gateId)
        assertEquals("closed", snapshot.mapFeatures[1].gateStatus)
        assertEquals("fence-1", snapshot.mapFeatures.last().fenceSectionId)
        assertEquals("bad", snapshot.mapFeatures.last().fenceCondition)
        assertEquals(2, snapshot.calendarItemCount)
        assertEquals("task-1", snapshot.calendarItems.first().taskId)
        assertEquals("Confirm level", snapshot.calendarItems.first().description)
        assertEquals("todo", snapshot.calendarItems.first().stage)
        assertEquals("Field Team", snapshot.calendarItems.first().assigneeName)
        assertEquals(true, snapshot.calendarItems.first().entityLinks.first().entityId == "paddock-1")
        assertEquals("incident-1", snapshot.calendarItems.last().incidentId)
        assertEquals("Incident", snapshot.calendarItems.last().stageLabel)
        assertEquals(1, snapshot.decisionCount)
        assertEquals("farm-1", snapshot.decisionFeed.first().farmId)
        assertEquals("North Block", snapshot.decisionFeed.first().farmName)
    }

    @Test
    fun snapshotParserKeepsShearingSessionsAndBreakdowns() {
        val snapshot = FarmSnapshot.fromJson(
            JSONObject()
                .put("farm", JSONObject().put("id", "farm-1").put("name", "North Block").put("timezone", "SAST"))
                .put(
                    "shearers",
                    JSONArray().put(
                        JSONObject()
                            .put("id", "shearer-1")
                            .put("farm_id", "farm-1")
                            .put("name", "Lootjie")
                            .put("active", true),
                    ),
                )
                .put(
                    "shearing_bale_codes",
                    JSONArray().put(
                        JSONObject()
                            .put("id", "code-1")
                            .put("species", "Sheep")
                            .put("code", "FH")
                            .put("active", true)
                            .put("line_type", "Fleece")
                            .put("age_group", "Adult")
                            .put("fineness_grade", "Fine")
                            .put("length_code", "B")
                            .put("clean_yield_percent", 80.0)
                            .put("style_character", "Good character")
                            .put("consistency", "Even")
                            .put("fault", "None")
                            .put("fineness_micron", 21.5),
                    ),
                )
                .put(
                    "shearing_sessions",
                    JSONArray().put(
                        JSONObject()
                            .put("id", "session-1")
                            .put("farm_id", "farm-1")
                            .put("name", "October sheep")
                            .put("species", "Sheep")
                            .put("start_date", "2026-10-01")
                            .put("end_date", "2026-10-03")
                            .put("status", "open")
                            .put("lootjie_rate", 10.0)
                            .put("adult_old_ram_multiplier", 2.0)
                            .put("notes", "Main shearing")
                            .put("totals", JSONObject().put("quantity", 4).put("amount", 80.0))
                            .put(
                                "bale_money_totals",
                                JSONObject()
                                    .put("total_bales", 2)
                                    .put("total_kg", 150.0)
                                    .put("priced_bales", 1)
                                    .put("priced_kg", 80.0)
                                    .put("unpriced_bales", 1)
                                    .put("total_price", 1600.0)
                                    .put("average_price_per_kg", 20.0),
                            )
                            .put(
                                "bale_summary_by_code",
                                JSONArray().put(
                                    JSONObject()
                                        .put("bale_code_id", "code-1")
                                        .put("code", "FH")
                                        .put("code_text", "FH")
                                        .put("bale_count", 2)
                                        .put("kg", 150.0)
                                        .put("priced_kg", 80.0)
                                        .put("unpriced_bales", 1)
                                        .put("total_price", 1600.0)
                                        .put("average_price_per_kg", 20.0),
                                ),
                            )
                            .put(
                                "bales",
                                JSONArray()
                                    .put(
                                        JSONObject()
                                            .put("id", "bale-1")
                                            .put("session_id", "session-1")
                                            .put("bale_code_id", "code-1")
                                            .put("code", "FH")
                                            .put("code_text", "FH")
                                            .put("bale_number", "1")
                                            .put("weight_kg", 80.0)
                                            .put("price_per_kg", 20.0)
                                            .put("total_price", 1600.0)
                                            .put("pricing_input_mode", "price_per_kg"),
                                    )
                                    .put(
                                        JSONObject()
                                            .put("id", "bale-2")
                                            .put("session_id", "session-1")
                                            .put("bale_code_id", "code-1")
                                            .put("code", "FH")
                                            .put("code_text", "FH")
                                            .put("bale_number", "2")
                                            .put("weight_kg", 70.0)
                                            .put("pricing_input_mode", "unpriced"),
                                    ),
                            )
                            .put(
                                "entries",
                                JSONArray().put(
                                    JSONObject()
                                        .put("id", "entry-1")
                                        .put("session_id", "session-1")
                                        .put("work_date", "2026-10-01")
                                        .put("shearer_id", "shearer-1")
                                        .put("shearer_name", "Lootjie")
                                        .put("animal_group_type_id", "group-1")
                                        .put(
                                            "animal_group_type",
                                            JSONObject()
                                                .put("id", "group-1")
                                                .put("species", "Sheep")
                                                .put("breed", "Merino")
                                                .put("sex", "ram")
                                                .put("age_class", "adult"),
                                        )
                                        .put("quantity", 4)
                                        .put("multiplier", 2.0)
                                        .put("unit_rate", 20.0)
                                        .put("line_amount", 80.0)
                                        .put("note", "Strong line"),
                                ),
                            )
                            .put(
                                "by_shearer",
                                JSONArray().put(
                                    JSONObject()
                                        .put("shearer_id", "shearer-1")
                                        .put("shearer_name", "Lootjie")
                                        .put("quantity", 4)
                                        .put("amount", 80.0),
                                ),
                            )
                            .put(
                                "by_animal_type",
                                JSONArray().put(
                                    JSONObject()
                                        .put("animal_group_type_id", "group-1")
                                        .put(
                                            "animal_group_type",
                                            JSONObject()
                                                .put("id", "group-1")
                                                .put("species", "Sheep")
                                                .put("breed", "Merino")
                                                .put("sex", "ram")
                                                .put("age_class", "adult"),
                                        )
                                        .put("quantity", 4)
                                        .put("amount", 80.0),
                                ),
                            )
                            .put(
                                "by_date",
                                JSONArray().put(
                                    JSONObject()
                                        .put("work_date", "2026-10-01")
                                        .put("quantity", 4)
                                        .put("amount", 80.0),
                                ),
                            ),
                    ),
                )
        )

        assertEquals(1, snapshot.shearerCount)
        assertEquals("Lootjie", snapshot.shearers.first().name)
        assertEquals(true, snapshot.shearers.first().active)
        assertEquals(1, snapshot.shearingBaleCodeCount)
        assertEquals("Fine", snapshot.shearingBaleCodes.first().finenessGrade)
        assertEquals("B", snapshot.shearingBaleCodes.first().lengthCode)
        assertEquals(80.0, snapshot.shearingBaleCodes.first().cleanYieldPercent ?: 0.0, 0.0)
        assertEquals("Even", snapshot.shearingBaleCodes.first().consistency)
        assertEquals(1, snapshot.shearingSessionCount)
        val session = snapshot.shearingSessions.first()
        assertEquals("October sheep", session.name)
        assertEquals("Open", session.statusLabel)
        assertEquals(4, session.totalQuantity)
        assertEquals(80.0, session.totalAmount, 0.0)
        assertEquals(2, session.baleMoneyTotals.totalBales)
        assertEquals(150.0, session.baleMoneyTotals.totalKg, 0.0)
        assertEquals(20.0, session.baleMoneyTotals.averagePricePerKg ?: 0.0, 0.0)
        assertEquals("FH", session.baleSummaryByCode.first().code)
        assertEquals(1, session.baleMoneyTotals.unpricedBales)
        assertEquals(2, session.bales.size)
        assertEquals(1600.0, session.bales.first().totalPrice ?: 0.0, 0.0)
        assertEquals(2.0, session.entries.first().multiplier, 0.0)
        assertEquals(20.0, session.entries.first().unitRate, 0.0)
        assertEquals("Sheep Merino ram adult", session.entries.first().animalGroupType.label)
        assertEquals("Strong line", session.entries.first().note)
        assertEquals(80.0, session.byShearer.first().amount, 0.0)
        assertEquals(4, session.byAnimalType.first().quantity)
        assertEquals("2026-10-01", session.byDate.first().workDate)
    }

    @Test
    fun bootstrapParserKeepsFormOptions() {
        val bootstrap = BootstrapResult.fromJson(
            JSONObject()
                .put(
                    "farms",
                    JSONArray()
                        .put(
                            JSONObject()
                                .put("id", "farm-1")
                                .put("name", "North Block")
                                .put("timezone", "Africa/Johannesburg")
                                .put("role", "manager")
                        )
                        .put(
                            JSONObject()
                                .put("id", "farm-2")
                                .put("name", "South Block")
                                .put("timezone", "SAST")
                        )
                )
                .put("animal_group_types", JSONArray())
                .put(
                    "sync",
                    JSONObject().put("supported_command_types", JSONArray().put("task.create"))
                )
                .put(
                    "form_options",
                    JSONObject()
                        .put(
                            "species_options",
                            JSONArray()
                                .put(JSONObject().put("value", "Cattle").put("label", "Cattle"))
                                .put(JSONObject().put("value", "Sheep").put("label", "Sheep"))
                        )
                        .put(
                            "sex_options_by_species",
                            JSONObject().put(
                                "Sheep",
                                JSONArray().put(JSONObject().put("value", "ewe").put("label", "Ewe"))
                            )
                        )
                        .put(
                            "age_class_options_by_species",
                            JSONObject().put(
                                "Sheep",
                                JSONArray().put(JSONObject().put("value", "lamb").put("label", "Lamb"))
                            )
                        )
                        .put(
                            "task_statuses",
                            JSONArray().put(JSONObject().put("value", "todo").put("label", "TO DO"))
                        )
                        .put(
                            "water_status_options_by_type",
                            JSONObject().put(
                                "tank",
                                JSONArray().put(JSONObject().put("value", "operational").put("label", "Operational"))
                            )
                        )
                        .put("water_level_asset_types", JSONArray().put("tank"))
                        .put(
                            "water_level_options",
                            JSONArray().put(JSONObject().put("value", "full").put("label", "Full"))
                        )
                )
        )

        assertEquals("task.create", bootstrap.supportedCommandTypes.first())
        assertEquals(2, bootstrap.farms.size)
        assertEquals("manager", bootstrap.farms.first().role)
        assertEquals("North Block - Manager", bootstrap.farms.first().displayLabel)
        assertEquals(null, bootstrap.farms.last().role)
        assertEquals("South Block", bootstrap.farms.last().displayLabel)
        assertEquals("Cattle", bootstrap.formOptions.speciesOptions.first().value)
        assertEquals("ewe", bootstrap.formOptions.sexOptionsBySpecies["Sheep"]?.first()?.value)
        assertEquals("lamb", bootstrap.formOptions.ageClassOptionsBySpecies["Sheep"]?.first()?.value)
        assertEquals("todo", bootstrap.formOptions.taskStatuses.first().value)
        assertEquals("operational", bootstrap.formOptions.waterStatusOptionsByType["tank"]?.first()?.value)
        assertEquals(true, "tank" in bootstrap.formOptions.waterLevelAssetTypes)
    }

    @Test
    fun syncSummaryCountsAppliedAndFailedResults() {
        val summary = SyncSummary(
            results = listOf(
                SyncResult("rain-1", "rainfall.create", "applied", false, null, null),
                SyncResult("note-1", "mob_event.create", "failed", false, "Mob not found", null),
            ),
            remainingQueueCount = 1,
        )

        assertEquals(1, summary.appliedCount)
        assertEquals(1, summary.failedCount)
        assertEquals(1, summary.remainingQueueCount)
    }

    @Test
    fun syncResultKeepsResponsePayload() {
        val result = SyncResult.fromJson(
            JSONObject()
                .put("client_command_id", "task-create-1")
                .put("type", "task.create")
                .put("status", "applied")
                .put("duplicate", false)
                .put("response", JSONObject().put("task", JSONObject().put("id", "task-1")))
        )

        assertEquals("task-1", result.response?.getJSONObject("task")?.getString("id"))
    }
}
