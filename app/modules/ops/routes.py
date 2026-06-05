from datetime import datetime
from pathlib import Path

from flask import Blueprint, current_app, render_template

from app.core.sqlite import sqlite_database_path

bp = Blueprint("ops", __name__, url_prefix="/ops")


@bp.get("")
def index():
    backup_dir = Path(current_app.config["BACKUP_DIR"])
    backup_files = sorted(backup_dir.glob("agritrack-backup-*.zip"), reverse=True)
    latest_backup = backup_files[0] if backup_files else None
    database_path = sqlite_database_path(current_app)
    instance_path = Path(current_app.instance_path)
    return render_template(
        "ops/index.html",
        database_path=database_path,
        backup_dir=backup_dir,
        backup_count=len(backup_files),
        latest_backup=latest_backup,
        latest_backup_mtime=_mtime(latest_backup),
        maps_dir=instance_path / "maps",
        attachments_dir=instance_path / "task_attachments",
        note_attachments_dir=instance_path / "note_attachments",
    )


def _mtime(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
