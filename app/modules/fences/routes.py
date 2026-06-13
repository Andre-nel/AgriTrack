from collections import Counter, defaultdict
import xml.etree.ElementTree as ET

from flask import current_app, flash, g, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Farm, FenceEvent, FenceEventMaterial, FenceSection, Paddock
from app.modules.fences.forms import material_rows_from_form, section_payload_from_form
from app.modules.tasks.entity_links import linked_task_rows_by_fence_section, linked_task_rows_for_entity
from app.services.fence_service import FenceService
from app.services.note_attachment_service import NoteAttachmentService


def _section_or_404(farm_id: str, fence_section_id: str) -> FenceSection:
    return FenceSection.query.filter_by(id=fence_section_id, farm_id=farm_id).first_or_404()


def _event_rows(section: FenceSection) -> list[dict]:
    rows = []
    for event in (
        FenceEvent.query.filter_by(fence_section_id=section.id)
        .order_by(FenceEvent.event_at.desc(), FenceEvent.created_at.desc())
        .all()
    ):
        rows.append(
            {
                **FenceService.serialize_event(event),
                "attachments": sorted(
                    event.attachments,
                    key=lambda attachment: attachment.created_at,
                    reverse=True,
                ),
            }
        )
    return rows


def _normalize_filter_tag(value: str | None) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _fence_filter_state() -> dict:
    condition = (request.args.get("condition") or "").strip()
    construction_type = (request.args.get("construction_type") or "").strip()
    section_type = (request.args.get("section_type") or "").strip()
    electric_wire = (request.args.get("electric_wire") or "").strip()
    return {
        "q": (request.args.get("q") or "").strip(),
        "condition": condition if condition in FenceService.CONDITIONS else "",
        "construction_type": construction_type if construction_type in FenceService.CONSTRUCTION_TYPES else "",
        "section_type": section_type if section_type in {FenceSection.TYPE_BOUNDARY, FenceSection.TYPE_INTERNAL} else "",
        "electric_wire": electric_wire if electric_wire in {"yes", "no"} else "",
        "tag": _normalize_filter_tag(request.args.get("tag")),
    }


def _available_section_tags(sections: list[FenceSection]) -> list[str]:
    tags = {
        tag
        for section in sections
        for tag in FenceService.tags_from_csv(section.tags_csv)
    }
    return sorted(tags)


def _filter_sections(sections: list[FenceSection], filters: dict) -> list[FenceSection]:
    query_text = str(filters.get("q") or "").casefold()
    filter_tag = _normalize_filter_tag(filters.get("tag"))
    rows = []
    for section in sections:
        tags = FenceService.tags_from_csv(section.tags_csv)
        if filters.get("condition") and section.condition != filters["condition"]:
            continue
        if filters.get("construction_type") and section.construction_type != filters["construction_type"]:
            continue
        if filters.get("section_type") and section.section_type != filters["section_type"]:
            continue
        if filters.get("electric_wire") == "yes" and not section.electric_wire:
            continue
        if filters.get("electric_wire") == "no" and section.electric_wire:
            continue
        if filter_tag and filter_tag not in tags:
            continue
        if query_text:
            searchable = " ".join(
                value
                for value in (
                    section.name,
                    section.notes,
                    section.paddock_a.name if section.paddock_a else "",
                    section.paddock_b.name if section.paddock_b else "",
                    " ".join(tags),
                )
                if value
            ).casefold()
            if query_text not in searchable:
                continue
        rows.append(section)
    return rows


def _length_m(section: FenceSection) -> float:
    return float(section.length_m or 0)


def _total_length_m(sections: list[FenceSection]) -> float:
    return round(sum(_length_m(section) for section in sections), 1)


def _section_breakdown(
    sections: list[FenceSection],
    *,
    field_name: str,
    values: tuple[str, ...],
    labels: dict[str, str],
) -> list[dict]:
    rows = []
    for value in values:
        matches = [section for section in sections if getattr(section, field_name) == value]
        rows.append(
            {
                "value": value,
                "label": labels.get(value, value.replace("_", " ").title()),
                "count": len(matches),
                "length_m": _total_length_m(matches),
            }
        )
    return rows


def _electric_breakdown(sections: list[FenceSection]) -> list[dict]:
    rows = []
    for value, label in ((True, "Electric Wire"), (False, "No Electric Wire")):
        matches = [section for section in sections if bool(section.electric_wire) is value]
        rows.append({"label": label, "count": len(matches), "length_m": _total_length_m(matches)})
    return rows


def _tag_stats(sections: list[FenceSection]) -> list[dict]:
    counts = Counter(
        tag
        for section in sections
        for tag in FenceService.tags_from_csv(section.tags_csv)
    )
    return [{"tag": tag, "count": count} for tag, count in counts.most_common()]


def _event_breakdown(events: list[FenceEvent]) -> list[dict]:
    counts = Counter(event.event_type for event in events)
    return [
        {
            "value": value,
            "label": FenceService.EVENT_TYPE_LABELS.get(value, value.replace("_", " ").title()),
            "count": counts.get(value, 0),
        }
        for value in FenceService.EVENT_TYPES
    ]


def _material_breakdown(materials: list[FenceEventMaterial]) -> list[dict]:
    rows_by_key = defaultdict(lambda: {"quantity": 0.0, "line_count": 0})
    for material in materials:
        key = (material.material_type, material.action, material.unit or "")
        rows_by_key[key]["line_count"] += 1
        if material.quantity is not None:
            rows_by_key[key]["quantity"] += float(material.quantity)
    rows = []
    for (material_type, action, unit), values in rows_by_key.items():
        rows.append(
            {
                "material_type": material_type,
                "material_type_label": FenceService.MATERIAL_TYPE_LABELS.get(
                    material_type,
                    material_type.replace("_", " ").title(),
                ),
                "action": action,
                "action_label": FenceService.MATERIAL_ACTION_LABELS.get(action, action.replace("_", " ").title()),
                "unit": unit,
                "quantity": round(values["quantity"], 2),
                "line_count": values["line_count"],
            }
        )
    return sorted(rows, key=lambda row: (row["material_type_label"], row["action_label"], row["unit"]))


def _fence_statistics(farm: Farm, sections: list[FenceSection]) -> dict:
    section_ids = [str(section.id) for section in sections]
    events = (
        FenceEvent.query.filter(FenceEvent.farm_id == farm.id)
        .order_by(FenceEvent.event_at.desc(), FenceEvent.created_at.desc())
        .all()
    )
    event_ids = [event.id for event in events]
    materials = (
        FenceEventMaterial.query.filter(FenceEventMaterial.event_id.in_(event_ids)).all()
        if event_ids
        else []
    )
    task_rows_by_section = linked_task_rows_by_fence_section(section_ids, farm.timezone)
    task_section_rows = []
    for section in sections:
        task_rows = task_rows_by_section.get(str(section.id), [])
        if task_rows:
            task_section_rows.append(
                {
                    "section": FenceService.serialize_section(section),
                    "task_count": len(task_rows),
                    "open_task_count": len([task for task in task_rows if task["status"] != "closed"]),
                }
            )
    task_section_rows.sort(key=lambda row: (-row["open_task_count"], -row["task_count"], row["section"]["name"].lower()))

    needs_attention_sections = [
        section for section in sections if section.condition in {"bad", "critical"}
    ]
    electric_sections = [section for section in sections if section.electric_wire]

    return {
        "summary": {
            "active_count": len(sections),
            "total_length_m": _total_length_m(sections),
            "needs_attention_count": len(needs_attention_sections),
            "needs_attention_length_m": _total_length_m(needs_attention_sections),
            "electric_count": len(electric_sections),
            "electric_length_m": _total_length_m(electric_sections),
            "event_count": len(events),
            "maintenance_event_count": len([event for event in events if event.event_type == "maintenance"]),
            "material_line_count": len(materials),
            "linked_task_count": sum(len(rows) for rows in task_rows_by_section.values()),
        },
        "by_condition": _section_breakdown(
            sections,
            field_name="condition",
            values=FenceService.CONDITIONS,
            labels=FenceService.CONDITION_LABELS,
        ),
        "by_construction": _section_breakdown(
            sections,
            field_name="construction_type",
            values=FenceService.CONSTRUCTION_TYPES,
            labels=FenceService.CONSTRUCTION_LABELS,
        ),
        "by_type": _section_breakdown(
            sections,
            field_name="section_type",
            values=(FenceSection.TYPE_BOUNDARY, FenceSection.TYPE_INTERNAL),
            labels={"boundary": "Boundary", "internal": "Internal"},
        ),
        "by_height": _section_breakdown(
            sections,
            field_name="height_profile",
            values=FenceService.HEIGHT_PROFILES,
            labels=FenceService.HEIGHT_LABELS,
        ),
        "by_electric": _electric_breakdown(sections),
        "tag_rows": _tag_stats(sections),
        "event_rows": _event_breakdown(events),
        "material_rows": _material_breakdown(materials),
        "task_section_rows": task_section_rows[:10],
        "recent_events": [FenceService.serialize_event(event, include_materials=False) for event in events[:10]],
    }


def register_legacy_routes(bp) -> None:
    @bp.get("/farms/<farm_id>/fences")
    def farm_fences(farm_id):
        farm = Farm.query.get_or_404(farm_id)
        all_sections = FenceService.sections_for_farm(str(farm.id))
        filter_state = _fence_filter_state()
        sections = _filter_sections(all_sections, filter_state)
        task_rows_by_section = linked_task_rows_by_fence_section(
            [str(section.id) for section in sections],
            farm.timezone,
        )
        section_rows = [
            {
                **FenceService.serialize_section(section),
                "task_count": len(task_rows_by_section.get(str(section.id), [])),
            }
            for section in sections
        ]
        return render_template(
            "fences/index.html",
            farm=farm,
            section_rows=section_rows,
            total_section_count=len(all_sections),
            filter_state=filter_state,
            has_active_filters=any(filter_state.values()),
            available_tags=_available_section_tags(all_sections),
            paddock_options=Paddock.query.filter_by(farm_id=farm.id, status="active").order_by(Paddock.name.asc()).all(),
            options=FenceService.template_options(),
        )

    @bp.get("/farms/<farm_id>/fences/statistics")
    def farm_fence_statistics(farm_id):
        farm = Farm.query.get_or_404(farm_id)
        sections = FenceService.sections_for_farm(str(farm.id))
        return render_template(
            "fences/statistics.html",
            farm=farm,
            stats=_fence_statistics(farm, sections),
        )

    @bp.post("/farms/<farm_id>/fences/sync")
    def sync_farm_fences_form(farm_id):
        farm = Farm.query.get_or_404(farm_id)
        try:
            result = FenceService.sync_auto_sections_for_farm(farm, current_app.instance_path)
            db.session.commit()
            flash(
                "Fence refresh complete: "
                f"{result['created']} created, {result['updated']} updated, {result['retired']} retired.",
                "success",
            )
        except (FileNotFoundError, ET.ParseError, ValueError) as exc:
            db.session.rollback()
            flash(f"Fence refresh failed: {exc}", "error")
        return redirect(url_for("web.farm_fences", farm_id=farm_id))

    @bp.get("/farms/<farm_id>/fences/<fence_section_id>")
    def farm_fence_detail(farm_id, fence_section_id):
        farm = Farm.query.get_or_404(farm_id)
        section = _section_or_404(farm_id, fence_section_id)
        return render_template(
            "fences/detail.html",
            farm=farm,
            section=section,
            section_row=FenceService.serialize_section(section),
            event_rows=_event_rows(section),
            linked_task_rows=linked_task_rows_for_entity(
                fence_section_id=str(section.id),
                tz_name=farm.timezone,
            ),
            options=FenceService.template_options(),
        )

    @bp.post("/farms/<farm_id>/fences/<fence_section_id>/edit")
    def update_farm_fence_form(farm_id, fence_section_id):
        Farm.query.get_or_404(farm_id)
        section = _section_or_404(farm_id, fence_section_id)
        try:
            FenceService.update_section(section, section_payload_from_form(request.form))
            db.session.commit()
            flash("Fence section updated", "success")
        except (IntegrityError, ValueError) as exc:
            db.session.rollback()
            flash(f"Fence section could not be updated: {exc}", "error")
        return redirect(url_for("web.farm_fence_detail", farm_id=farm_id, fence_section_id=fence_section_id))

    @bp.post("/farms/<farm_id>/fences/<fence_section_id>/events")
    def create_farm_fence_event_form(farm_id, fence_section_id):
        Farm.query.get_or_404(farm_id)
        section = _section_or_404(farm_id, fence_section_id)
        try:
            event = FenceService.create_event(
                section=section,
                event_type=request.form.get("event_type"),
                description=request.form.get("description"),
                raw_tags=request.form.get("event_tags"),
                condition_after=request.form.get("condition_after"),
                material_rows=material_rows_from_form(request.form),
            )
            attachments = NoteAttachmentService.create_attachments_from_uploads(
                request.files.getlist("event_images"),
                farm_id=str(section.farm_id),
                event_type="fence_event",
                event_id=str(event.id),
                instance_path=current_app.instance_path,
                uploaded_by_user_id=(
                    str(g.web_user.id) if getattr(g, "web_user", None) is not None else None
                ),
            )
            db.session.commit()
            suffix = f" with {len(attachments)} image(s)" if attachments else ""
            flash(f"Fence event recorded{suffix}", "success")
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
        return redirect(url_for("web.farm_fence_detail", farm_id=farm_id, fence_section_id=fence_section_id))
