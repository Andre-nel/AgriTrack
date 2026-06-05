from flask import Blueprint, abort, current_app, send_file

from app.modules.analytics.routes import register_legacy_routes as register_analytics_routes
from app.modules.dashboard.routes import register_legacy_routes as register_dashboard_routes
from app.modules.farms.map_routes import register_legacy_routes as register_farm_map_routes
from app.modules.farms.routes import register_legacy_routes as register_farm_routes
from app.modules.grazing.routes import register_legacy_routes as register_grazing_routes
from app.modules.imports.routes import register_legacy_routes as register_import_routes
from app.modules.mobs.routes import register_legacy_routes as register_mob_routes
from app.modules.paddocks.routes import register_legacy_routes as register_paddock_routes
from app.modules.water.routes import register_legacy_routes as register_water_routes
from app.models import NoteAttachment
from app.services.note_attachment_service import NoteAttachmentService

bp = Blueprint("web", __name__)
register_analytics_routes(bp)
register_dashboard_routes(bp)
register_import_routes(bp)
register_farm_routes(bp)
register_farm_map_routes(bp)
register_grazing_routes(bp)
register_mob_routes(bp)
register_paddock_routes(bp)
register_water_routes(bp)


@bp.get("/note-attachments/<attachment_id>")
def note_attachment_file(attachment_id):
    attachment = NoteAttachment.query.filter_by(id=attachment_id).first_or_404()
    try:
        target = NoteAttachmentService.absolute_attachment_path(
            current_app.instance_path,
            attachment.storage_path,
        )
    except FileNotFoundError:
        abort(404)
    if not target.exists():
        abort(404)
    return send_file(
        target,
        mimetype=attachment.content_type,
        as_attachment=False,
        download_name=attachment.original_filename,
    )
