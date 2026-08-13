from decimal import InvalidOperation

from flask import current_app, flash, g, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Farm, Mob, Paddock
from app.modules.mobs.forms import (
    parse_count_move_allocations,
    parse_move_allocations,
    parse_split_rows,
    parse_transfer_rows,
)
from app.modules.mobs.presenters import build_mob_detail_context
from app.modules.mobs.services import adjust_mob_stock_from_form, update_mob_balance_line_from_form
from app.services.mob_event_service import MobEventService
from app.services.mob_service import MobService
from app.services.movement_service import MovementService
from app.services.note_attachment_service import NoteAttachmentService


def _get_active_mob_or_404(mob_id: str) -> Mob:
    return Mob.query.filter_by(id=mob_id, status="active").first_or_404()


def register_legacy_routes(bp) -> None:
    @bp.get("/mobs/<mob_id>")
    def mob_detail(mob_id):
        mob = _get_active_mob_or_404(mob_id)
        selected_event_tag = " ".join(
            (request.args.get("event_tag") or "").strip().lower().split()
        )
        context = build_mob_detail_context(mob, selected_event_tag)
        return render_template("mob_detail.html", **context)

    @bp.post("/mobs/<mob_id>/adjust")
    def mob_adjust_form(mob_id):
        mob = _get_active_mob_or_404(mob_id)
        try:
            flash(adjust_mob_stock_from_form(mob, request.form), "success")
        except ValueError as exc:
            flash(str(exc), "error")

        return redirect(url_for("web.mob_detail", mob_id=mob_id))

    @bp.post("/mobs/<mob_id>/balances/edit")
    def mob_edit_balance_form(mob_id):
        mob = _get_active_mob_or_404(mob_id)
        try:
            flash(update_mob_balance_line_from_form(mob, request.form), "success")
        except ValueError as exc:
            flash(str(exc), "error")

        return redirect(url_for("web.mob_detail", mob_id=mob_id))

    @bp.post("/mobs/<mob_id>/events")
    def mob_event_create_form(mob_id):
        mob = _get_active_mob_or_404(mob_id)
        tags_text = request.form.get("event_tags")
        description = request.form.get("event_description")

        try:
            event = MobEventService.create_event(
                mob_id=mob.id,
                farm_id=mob.farm_id,
                description=description,
                raw_tags=tags_text,
            )
            db.session.flush()
            attachments = NoteAttachmentService.create_attachments_from_uploads(
                request.files.getlist("event_images"),
                farm_id=str(mob.farm_id),
                event_type="mob_event",
                event_id=str(event.id),
                instance_path=current_app.instance_path,
                uploaded_by_user_id=(
                    str(g.web_user.id) if getattr(g, "web_user", None) is not None else None
                ),
            )
            db.session.commit()
            suffix = f" with {len(attachments)} image(s)" if attachments else ""
            flash(f"Mob note recorded{suffix}", "success")
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")

        return redirect(url_for("web.mob_detail", mob_id=mob_id))

    @bp.post("/mobs/<mob_id>/rename")
    def mob_rename_form(mob_id):
        mob = _get_active_mob_or_404(mob_id)
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Mob name is required", "error")
            return redirect(url_for("web.mob_detail", mob_id=mob_id))

        if name == mob.name:
            flash("Mob name unchanged", "success")
            return redirect(url_for("web.mob_detail", mob_id=mob_id))

        mob.name = name
        try:
            db.session.commit()
            flash("Mob name updated", "success")
        except IntegrityError:
            db.session.rollback()
            flash("Active mob name already exists on this farm", "error")

        return redirect(url_for("web.mob_detail", mob_id=mob_id))

    @bp.post("/mobs/<mob_id>/move")
    def mob_move_form(mob_id):
        mob = _get_active_mob_or_404(mob_id)
        allocation_mode = (request.form.get("allocation_mode") or "percentage").strip().lower()

        try:
            valid_farm_ids = {str(farm.id) for farm in Farm.query.all()}
            valid_paddock_farm_ids = {str(p.id): str(p.farm_id) for p in Paddock.query.all()}
            valid_paddock_ids = set(valid_paddock_farm_ids)
            if allocation_mode == "counts":
                allocations = parse_count_move_allocations(
                    paddock_ids=request.form.getlist("count_paddock_id"),
                    farm_ids=request.form.getlist("count_farm_id"),
                    group_ids=request.form.getlist("count_group_id"),
                    head_counts=request.form.getlist("count_head_count"),
                    valid_farm_ids=valid_farm_ids,
                    valid_paddock_ids=valid_paddock_ids,
                    valid_paddock_farm_ids=valid_paddock_farm_ids,
                )
            else:
                allocation_mode = "percentage"
                allocations = parse_move_allocations(
                    paddock_ids=request.form.getlist("paddock_id"),
                    allocation_pcts=request.form.getlist("allocation_pct"),
                    farm_ids=request.form.getlist("allocation_farm_id"),
                    valid_farm_ids=valid_farm_ids,
                    valid_paddock_ids=valid_paddock_ids,
                    valid_paddock_farm_ids=valid_paddock_farm_ids,
                )
            destination_farm_id = valid_paddock_farm_ids.get(str(allocations[0]["paddock_id"]))
            if not destination_farm_id:
                raise ValueError("Destination farm is required")

            MovementService.move_mob(
                mob=mob,
                allocations=allocations,
                destination_farm_id=destination_farm_id,
                allocation_mode=allocation_mode,
                allow_cross_farm_allocations=True,
            )
            db.session.commit()
            flash("Mob moved", "success")
        except IntegrityError:
            db.session.rollback()
            flash("Move failed: active mob name already exists on the destination farm", "error")
        except (ValueError, InvalidOperation) as exc:
            db.session.rollback()
            flash(str(exc), "error")

        return redirect(url_for("web.mob_detail", mob_id=mob_id))

    @bp.post("/mobs/<mob_id>/transfer")
    def mob_transfer_form(mob_id):
        source = _get_active_mob_or_404(mob_id)
        destination_farm_id = (
            request.form.get("transfer_destination_farm_id") or str(source.farm_id)
        ).strip()
        destination_mob_id = (request.form.get("transfer_destination_mob_id") or "").strip()
        transfer_group_ids = request.form.getlist("transfer_group_id")
        transfer_quantities = request.form.getlist("transfer_quantity")
        note = (request.form.get("transfer_note") or "").strip() or None

        try:
            if not destination_farm_id:
                raise ValueError("Destination farm is required")
            if not Farm.query.filter_by(id=destination_farm_id).first():
                raise ValueError("Destination farm is invalid")
            if not destination_mob_id:
                raise ValueError("Destination mob is required")

            destination_mob = Mob.query.filter_by(id=destination_mob_id, status="active").first()
            if not destination_mob:
                raise ValueError("Destination mob is invalid")
            if str(destination_mob.farm_id) != destination_farm_id:
                raise ValueError("Destination mob is invalid for the selected farm")

            transfers = parse_transfer_rows(transfer_group_ids, transfer_quantities)
            MovementService.transfer_stock_between_mobs(
                source_mob=source,
                destination_mob=destination_mob,
                transfers=transfers,
                destination_farm_id=destination_farm_id,
                note=note,
            )
            db.session.commit()
            flash(f"Transferred stock to {destination_mob.name}", "success")
        except IntegrityError:
            db.session.rollback()
            flash("Transfer failed due to a concurrent stock update. Please retry.", "error")
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")

        return redirect(url_for("web.mob_detail", mob_id=mob_id))

    @bp.post("/mobs/<mob_id>/deactivate")
    def mob_deactivate_form(mob_id):
        mob = _get_active_mob_or_404(mob_id)
        total_head_count = sum(int(balance.head_count) for balance in mob.balances)
        if total_head_count > 0:
            flash("Mob cannot be deactivated while it still contains stock", "error")
            return redirect(url_for("web.mob_detail", mob_id=mob_id))

        MobService.archive_mob(mob)
        db.session.commit()
        flash("Mob deactivated", "success")
        return redirect(url_for("web.farm_detail", farm_id=mob.farm_id))

    @bp.post("/mobs/<mob_id>/split")
    def mob_split_form(mob_id):
        source = _get_active_mob_or_404(mob_id)
        split_names = request.form.getlist("split_mob_name")
        split_group_ids = request.form.getlist("split_group_id")
        split_quantities = request.form.getlist("split_quantity")

        try:
            splits = parse_split_rows(split_names, split_group_ids, split_quantities)

            created = MovementService.split_mob(source_mob=source, splits=splits)
            db.session.commit()
            flash(f"Mob split complete. Created {len(created)} mobs.", "success")
        except IntegrityError:
            db.session.rollback()
            flash("Split failed: one or more new mob names already exist on this farm", "error")
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")

        return redirect(url_for("web.mob_detail", mob_id=mob_id))
