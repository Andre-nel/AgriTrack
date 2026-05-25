"""Create a timestamped private-farm backup for SQLite data and local assets."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import create_app  # noqa: E402
from app.config import get_config  # noqa: E402
from app.core.sqlite import sqlite_database_path  # noqa: E402

BACKUP_PREFIX = "agritrack-backup-"


def main() -> int:
    parser = argparse.ArgumentParser(description="Back up the AgriTrack SQLite database and assets.")
    parser.add_argument("--output-dir", help="Directory for backup zip files.")
    parser.add_argument("--keep", type=int, help="Number of newest backups to keep.")
    parser.add_argument("--no-prune", action="store_true", help="Do not delete old backups.")
    args = parser.parse_args()

    app = create_app(get_config())
    with app.app_context():
        source_db = sqlite_database_path(app)
        if source_db is None:
            raise SystemExit("Backup currently supports file-backed SQLite databases only.")
        if not source_db.exists():
            raise SystemExit(f"Database does not exist: {source_db}")

        backup_dir = Path(args.output_dir or app.config["BACKUP_DIR"]).resolve()
        backup_dir.mkdir(parents=True, exist_ok=True)
        keep = args.keep if args.keep is not None else int(app.config["BACKUP_RETENTION_COUNT"])
        created = create_backup(
            source_db=source_db,
            instance_dir=Path(app.instance_path),
            backup_dir=backup_dir,
        )
        if not args.no_prune:
            prune_old_backups(backup_dir, keep)

    print(created)
    return 0


def create_backup(*, source_db: Path, instance_dir: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    target = backup_dir / f"{BACKUP_PREFIX}{timestamp}.zip"

    with tempfile.TemporaryDirectory(prefix="agritrack-backup-") as tmp_name:
        tmp_dir = Path(tmp_name)
        db_copy = tmp_dir / "agritrack.db"
        backup_sqlite_database(source_db, db_copy)

        manifest = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_database": str(source_db),
            "includes": ["agritrack.db", "maps", "task_attachments"],
        }
        (tmp_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(db_copy, "agritrack.db")
            archive.write(tmp_dir / "manifest.json", "manifest.json")
            add_tree(archive, instance_dir / "maps", "maps")
            add_tree(archive, instance_dir / "task_attachments", "task_attachments")

    return target


def backup_sqlite_database(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(source)
    try:
        target_connection = sqlite3.connect(target)
        try:
            with target_connection:
                source_connection.backup(target_connection)
        finally:
            target_connection.close()
    finally:
        source_connection.close()


def add_tree(archive: zipfile.ZipFile, source_dir: Path, archive_root: str) -> None:
    if not source_dir.exists():
        return
    for path in sorted(source_dir.rglob("*")):
        if path.is_file():
            archive.write(path, str(Path(archive_root) / path.relative_to(source_dir)))


def prune_old_backups(backup_dir: Path, keep: int) -> None:
    if keep < 1:
        return
    backups = sorted(backup_dir.glob(f"{BACKUP_PREFIX}*.zip"), reverse=True)
    for old_backup in backups[keep:]:
        if old_backup.is_file():
            old_backup.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
