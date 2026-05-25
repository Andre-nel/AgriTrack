"""Verify that an AgriTrack backup zip can boot against a restored database."""

from __future__ import annotations

import argparse
import sys
import tempfile
import zipfile
from pathlib import Path

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import create_app  # noqa: E402
from app.config import TestingConfig, get_config  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Farm  # noqa: E402
from scripts.backup_agritrack import BACKUP_PREFIX  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Check that an AgriTrack backup can be restored.")
    parser.add_argument("backup", nargs="?", help="Backup zip path. Defaults to the newest backup.")
    parser.add_argument("--backup-dir", help="Directory to search when no backup path is supplied.")
    args = parser.parse_args()

    config = get_config()
    configured_app = create_app(config)
    backup_path = Path(args.backup).resolve() if args.backup else latest_backup(
        Path(args.backup_dir or configured_app.config["BACKUP_DIR"]).resolve()
    )
    if backup_path is None:
        raise SystemExit("No AgriTrack backup zip found.")
    if not backup_path.exists():
        raise SystemExit(f"Backup does not exist: {backup_path}")

    with tempfile.TemporaryDirectory(prefix="agritrack-restore-check-") as tmp_name:
        tmp_dir = Path(tmp_name)
        extract_dir = tmp_dir / "restore"
        extract_backup(backup_path, extract_dir)
        restored_db = extract_dir / "agritrack.db"
        if not restored_db.exists():
            raise SystemExit("Backup is missing agritrack.db")

        class RestoreCheckConfig(TestingConfig):
            SQLALCHEMY_DATABASE_URI = f"sqlite:///{restored_db.as_posix()}"
            WEB_AUTH_REQUIRED = False

        app = create_app(RestoreCheckConfig)
        with app.app_context():
            db.session.execute(text("SELECT 1")).scalar()
            farm_count = Farm.query.count()
            version = migration_version()
            db.session.remove()
            db.engine.dispose()

        map_count = count_files(extract_dir / "maps")
        attachment_count = count_files(extract_dir / "task_attachments")

    print(f"Restore check ok: {backup_path}")
    print(f"Farms: {farm_count}")
    print(f"Migration: {version or 'unknown'}")
    print(f"Map files: {map_count}")
    print(f"Task attachments: {attachment_count}")
    return 0


def latest_backup(backup_dir: Path) -> Path | None:
    backups = sorted(backup_dir.glob(f"{BACKUP_PREFIX}*.zip"), reverse=True)
    return backups[0] if backups else None


def extract_backup(backup_path: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(backup_path) as archive:
        archive.extractall(target_dir)


def migration_version() -> str | None:
    try:
        return db.session.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:
        return None


def count_files(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for item in path.rglob("*") if item.is_file())


if __name__ == "__main__":
    raise SystemExit(main())
