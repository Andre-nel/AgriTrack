from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from flask import current_app, flash, g, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Farm, GrazingAllocation, GrazingSession, Mob, Paddock, PaddockEvent
from app.modules.tasks.entity_links import linked_task_rows_for_entity
from app.services.gate_service import GateService
from app.services.fence_service import FenceService
from app.services.grazing_history_service import GrazingHistoryService
from app.services.movement_service import MovementService
from app.services.note_attachment_service import NoteAttachmentService
from app.services.paddock_service import PaddockService
from app.services.paddock_event_service import PaddockEventService
from app.services.reporting_service import ReportingService
from app.services.water_network_service import WaterNetworkService


def _parse_query_date(value: str | None) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    return date.fromisoformat(text)


def register_legacy_routes(bp) -> None:
    @bp.post("/paddocks/<paddock_id>/move-mobs")
    def paddock_move_all_mobs_form(paddock_id):
        paddock = Paddock.query.get_or_404(paddock_id)
        mob_ids = request.form.getlist("mob_id")
        destination_farm_ids = request.form.getlist("destination_farm_id")
        destination_paddock_ids = request.form.getlist("destination_paddock_id")

        try:
            if not mob_ids:
                raise ValueError("No active mobs on this paddock to move")
            if not (
                len(mob_ids) == len(destination_farm_ids) == len(destination_paddock_ids)
            ):
                raise ValueError(
                    "Move request is incomplete. Provide destination farm and paddock for each mob"
                )
            if len(set(mob_ids)) != len(mob_ids):
                raise ValueError("Duplicate mob rows are not allowed")

            active_allocations = (
                GrazingAllocation.query.join(GrazingSession).join(Mob)
                .filter(
                    GrazingAllocation.paddock_id == paddock_id,
                    GrazingSession.end_at.is_(None),
                    Mob.status == "active",
                )
                .all()
            )
            active_mob_ids = {
                str(allocation.grazing_session.mob_id)
                for allocation in active_allocations
                if allocation.grazing_session.mob.status == "active"
            }
            if set(mob_ids) != active_mob_ids:
                raise ValueError("Active mob list is out of date. Refresh and try again.")

            moved_count = 0
            for mob_id_raw, destination_farm_raw, destination_paddock_raw in zip(
                mob_ids,
                destination_farm_ids,
                destination_paddock_ids,
            ):
                mob_id = (mob_id_raw or "").strip()
                destination_farm_id = (destination_farm_raw or "").strip()
                destination_paddock_id = (destination_paddock_raw or "").strip()

                if not destination_farm_id:
                    raise ValueError("Destination farm is required for each mob")
                if not destination_paddock_id:
                    raise ValueError("Destination paddock is required for each mob")
                if destination_paddock_id == str(paddock.id):
                    raise ValueError("Destination paddock must be different from the current paddock")
                if mob_id not in active_mob_ids:
                    raise ValueError("One or more mobs are no longer active on this paddock")

                mob = Mob.query.filter_by(id=mob_id, status="active").first()
                if not mob:
                    raise ValueError("One or more selected mobs are invalid")

                MovementService.move_mob(
                    mob=mob,
                    allocations=[
                        {
                            "paddock_id": destination_paddock_id,
                            "allocation_fraction": "1.0",
                        }
                    ],
                    destination_farm_id=destination_farm_id,
                )
                moved_count += 1

            db.session.commit()
            flash(f"Moved {moved_count} mob(s) from {paddock.name}", "success")
        except IntegrityError:
            db.session.rollback()
            flash(
                "Move failed: active mob name already exists on one of the destination farms",
                "error",
            )
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")

        return redirect(url_for("web.paddock_detail", paddock_id=paddock_id))

    @bp.post("/paddocks/<paddock_id>/stocking-rate")
    def update_paddock_stocking_rate_form(paddock_id):
        paddock = Paddock.query.get_or_404(paddock_id)
        override_raw = (request.form.get("stocking_rate_ha_per_lsu_override") or "").strip()
        if not override_raw:
            paddock.stocking_rate_ha_per_lsu_override = None
            db.session.commit()
            flash("Paddock carrying capacity override cleared. Farm default now applies.", "success")
            return redirect(url_for("web.paddock_detail", paddock_id=paddock_id))

        try:
            override_value = Decimal(override_raw)
            if override_value <= 0:
                raise ValueError
        except (InvalidOperation, ValueError):
            flash("Paddock carrying capacity override (ha/LSU) must be greater than 0", "error")
            return redirect(url_for("web.paddock_detail", paddock_id=paddock_id))

        paddock.stocking_rate_ha_per_lsu_override = override_value
        db.session.commit()
        flash("Paddock carrying capacity override updated", "success")
        return redirect(url_for("web.paddock_detail", paddock_id=paddock_id))

    @bp.post("/paddocks/<paddock_id>/events")
    def paddock_event_create_form(paddock_id):
        paddock = Paddock.query.get_or_404(paddock_id)
        raw_tags = request.form.get("event_tags")
        description = request.form.get("event_description")

        try:
            event = PaddockEventService.create_event(
                paddock_id=paddock.id,
                farm_id=paddock.farm_id,
                description=description,
                raw_tags=raw_tags,
            )
            db.session.flush()
            attachments = NoteAttachmentService.create_attachments_from_uploads(
                request.files.getlist("event_images"),
                farm_id=str(paddock.farm_id),
                event_type="paddock_event",
                event_id=str(event.id),
                instance_path=current_app.instance_path,
                uploaded_by_user_id=(
                    str(g.web_user.id) if getattr(g, "web_user", None) is not None else None
                ),
            )
            db.session.commit()
            suffix = f" with {len(attachments)} image(s)" if attachments else ""
            flash(f"Paddock note recorded{suffix}", "success")
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")

        return redirect(url_for("web.paddock_detail", paddock_id=paddock_id))

    @bp.post("/paddocks/<paddock_id>/rename")
    def rename_paddock_form(paddock_id):
        paddock = Paddock.query.get_or_404(paddock_id)
        try:
            result = PaddockService.rename_paddock(
                paddock,
                request.form.get("name"),
                instance_path=current_app.instance_path,
            )
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return redirect(url_for("web.paddock_detail", paddock_id=paddock_id))
        except IntegrityError:
            db.session.rollback()
            flash("A paddock with this name already exists on this farm", "error")
            return redirect(url_for("web.paddock_detail", paddock_id=paddock_id))

        if not result["changed"]:
            flash("Paddock name unchanged", "success")
        elif result["map_updated"]:
            flash("Paddock name updated", "success")
        else:
            flash("Paddock name updated. No farm map file was found to update.", "success")
        return redirect(url_for("web.paddock_detail", paddock_id=paddock_id))

    @bp.get("/paddocks/<paddock_id>")
    def paddock_detail(paddock_id):
        paddock = Paddock.query.get_or_404(paddock_id)
        stock_summary = ReportingService.paddock_current_stock_summary(paddock_id)
        farms = Farm.query.order_by(Farm.name).all()
        all_paddocks = Paddock.query.filter_by(status="active").order_by(Paddock.name).all()
        bulk_move_farms = [{"id": str(farm.id), "name": farm.name} for farm in farms]
        bulk_move_paddocks_by_farm = {str(farm.id): [] for farm in farms}
        for option_paddock in all_paddocks:
            bulk_move_paddocks_by_farm.setdefault(str(option_paddock.farm_id), []).append(
                {"id": str(option_paddock.id), "name": option_paddock.name}
            )

        today = date.today()
        try:
            period_start_date = _parse_query_date(request.args.get("period_start")) or date(
                today.year,
                1,
                1,
            )
            period_end_date = _parse_query_date(request.args.get("period_end")) or today
        except ValueError:
            flash("Analysis dates must be valid (YYYY-MM-DD)", "error")
            return redirect(url_for("web.paddock_detail", paddock_id=paddock_id))

        if period_end_date < period_start_date:
            flash("Analysis end date must be on or after the start date", "error")
            return redirect(url_for("web.paddock_detail", paddock_id=paddock_id))

        period_start_dt = datetime.combine(period_start_date, time.min)
        period_end_dt = datetime.combine(period_end_date + timedelta(days=1), time.min)
        current_dt = datetime.utcnow()
        this_year_start_dt = datetime(today.year, 1, 1)

        grazeable_area_ha = float(paddock.grazeable_area_ha or 0)
        area_ha = float(paddock.area_ha or 0)
        effective_area_ha = grazeable_area_ha if grazeable_area_ha > 0 else area_ha

        farm_stocking_rate = float(paddock.farm.default_stocking_rate_ha_per_lsu)
        paddock_override_rate = (
            float(paddock.stocking_rate_ha_per_lsu_override)
            if paddock.stocking_rate_ha_per_lsu_override is not None
            else None
        )
        effective_stocking_rate = paddock_override_rate or farm_stocking_rate
        grazing_capacity_sdh = (
            365.0 / effective_stocking_rate if effective_stocking_rate > 0 else 0.0
        )

        current_lsu = ReportingService.paddock_current_lsu_breakdown(paddock_id)
        current_activity = ReportingService.paddock_continuous_activity(paddock)
        paddock_ha_per_current_lsu = (
            area_ha / current_lsu["total_lsu"] if current_lsu["total_lsu"] > 0 else None
        )
        current_stocking_density = (
            current_lsu["total_lsu"] / effective_area_ha if effective_area_ha > 0 else None
        )

        year_lsu_days = GrazingHistoryService.paddock_lsu_days_for_period(
            paddock,
            period_start=this_year_start_dt,
            period_end=current_dt,
        )
        sdh_used_this_year = (year_lsu_days / effective_area_ha) if effective_area_ha > 0 else None
        sdh_remaining_this_year = (
            grazing_capacity_sdh - sdh_used_this_year if sdh_used_this_year is not None else None
        )

        period_lsu_days = GrazingHistoryService.paddock_lsu_days_for_period(
            paddock,
            period_start=period_start_dt,
            period_end=period_end_dt,
        )
        period_days = (period_end_dt - period_start_dt).total_seconds() / 86400.0
        period_sdh_used = (period_lsu_days / effective_area_ha) if effective_area_ha > 0 else None
        period_avg_stocking_density = (
            (period_lsu_days / (effective_area_ha * period_days))
            if effective_area_ha > 0 and period_days > 0
            else None
        )

        history_allocations = sorted(
            paddock.grazing_allocations,
            key=lambda a: a.grazing_session.start_at,
            reverse=True,
        )
        active_allocations = (
            GrazingAllocation.query.join(GrazingSession).join(Mob)
            .filter(
                GrazingAllocation.paddock_id == paddock_id,
                GrazingSession.end_at.is_(None),
                Mob.status == "active",
            )
            .all()
        )
        bulk_move_mob_rows = []
        seen_mob_ids = set()
        for allocation in sorted(
            active_allocations,
            key=lambda row: row.grazing_session.mob.name.lower(),
        ):
            session = allocation.grazing_session
            mob = session.mob
            mob_id = str(mob.id)
            if mob.status != "active" or mob_id in seen_mob_ids:
                continue
            seen_mob_ids.add(mob_id)
            bulk_move_mob_rows.append(
                {
                    "mob_id": mob_id,
                    "mob_name": mob.name,
                    "allocation_pct": ReportingService.allocation_effective_fraction(allocation) * 100.0,
                    "default_destination_farm_id": str(mob.farm_id),
                }
            )

        history = []
        for allocation in history_allocations:
            session = allocation.grazing_session
            allocated_lsu = ReportingService.allocation_lsu(allocation)
            history.append(
                {
                    "allocation": allocation,
                    "allocation_pct": ReportingService.allocation_effective_fraction(allocation) * 100.0,
                    "allocated_lsu": allocated_lsu,
                    "mob_name": session.mob.name,
                }
            )
        local_water_assets = WaterNetworkService.local_assets_for_paddock(str(paddock.id))
        serving_water_assets = WaterNetworkService.service_assets_serving_paddock(str(paddock.id))
        paddock_events = [
            {
                "event_at": row.event_at,
                "tags": PaddockEventService.tags_from_csv(row.tags_csv),
                "description": row.description,
                "attachments": sorted(
                    row.attachments,
                    key=lambda attachment: attachment.created_at,
                    reverse=True,
                ),
            }
            for row in (
                PaddockEvent.query.filter_by(paddock_id=paddock.id)
                .order_by(PaddockEvent.event_at.desc(), PaddockEvent.created_at.desc())
                .all()
            )
        ]

        return render_template(
            "paddock_detail.html",
            paddock=paddock,
            stock_summary=stock_summary,
            history=history,
            area_ha=area_ha,
            current_lsu=current_lsu,
            current_activity=current_activity,
            paddock_ha_per_current_lsu=paddock_ha_per_current_lsu,
            effective_stocking_rate=effective_stocking_rate,
            farm_stocking_rate=farm_stocking_rate,
            paddock_override_rate=paddock_override_rate,
            grazing_capacity_sdh=grazing_capacity_sdh,
            sdh_used_this_year=sdh_used_this_year,
            sdh_remaining_this_year=sdh_remaining_this_year,
            current_stocking_density=current_stocking_density,
            period_start=period_start_date,
            period_end=period_end_date,
            period_sdh_used=period_sdh_used,
            period_avg_stocking_density=period_avg_stocking_density,
            period_lsu_days=period_lsu_days,
            effective_area_ha=effective_area_ha,
            bulk_move_farms=bulk_move_farms,
            bulk_move_paddocks_by_farm=bulk_move_paddocks_by_farm,
            bulk_move_mob_rows=bulk_move_mob_rows,
            local_water_assets=local_water_assets,
            serving_water_assets=serving_water_assets,
            paddock_events=paddock_events,
            water_asset_type_labels=WaterNetworkService.ASSET_TYPE_LABELS,
            adjacent_gate_rows=[
                {
                    **GateService.serialize_gate(gate),
                    "close_requirements": GateService.close_requirements(gate),
                }
                for gate in GateService.adjacent_gates_for_paddock(str(paddock.id))
            ],
            fence_section_rows=[
                FenceService.serialize_section(section)
                for section in FenceService.sections_for_paddock(str(paddock.id))
            ],
            linked_task_rows=linked_task_rows_for_entity(
                paddock_id=str(paddock.id),
                tz_name=paddock.farm.timezone if paddock.farm else "SAST",
            ),
        )
