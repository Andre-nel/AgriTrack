from pathlib import Path
from tempfile import NamedTemporaryFile
from xml.sax.saxutils import escape

from app.extensions import db
from app.models import Paddock


class PaddockService:
    MAX_NAME_LENGTH = 120

    @staticmethod
    def normalize_name(value: str | None) -> str:
        return " ".join((value or "").strip().split())

    @classmethod
    def validate_name(cls, value: str | None) -> str:
        name = cls.normalize_name(value)
        if not name:
            raise ValueError("Paddock name is required")
        if len(name) > cls.MAX_NAME_LENGTH:
            raise ValueError(f"Paddock name must be {cls.MAX_NAME_LENGTH} characters or fewer")
        if any(ord(char) < 32 for char in name):
            raise ValueError("Paddock name contains invalid characters")
        return name

    @classmethod
    def ensure_name_available(
        cls,
        farm_id: str,
        candidate_name: str,
        *,
        exclude_paddock_id: str | None = None,
    ) -> None:
        normalized_candidate = cls.normalize_name(candidate_name).casefold()
        existing_rows = Paddock.query.filter_by(farm_id=farm_id).all()
        for existing in existing_rows:
            if exclude_paddock_id and str(existing.id) == str(exclude_paddock_id):
                continue
            if cls.normalize_name(existing.name).casefold() == normalized_candidate:
                raise ValueError("A paddock with this name already exists on this farm")

    @classmethod
    def validate_available_name(
        cls,
        farm_id: str,
        candidate_name: str | None,
        *,
        exclude_paddock_id: str | None = None,
    ) -> str:
        name = cls.validate_name(candidate_name)
        cls.ensure_name_available(
            farm_id,
            name,
            exclude_paddock_id=exclude_paddock_id,
        )
        return name

    @classmethod
    def _map_path_for_farm(cls, farm_name: str, instance_path: str | Path) -> Path:
        return Path(instance_path) / "maps" / f"{farm_name}.kml"

    @classmethod
    def _rename_paddock_in_kml(
        cls,
        farm_name: str,
        old_name: str,
        new_name: str,
        *,
        instance_path: str | Path,
    ) -> dict:
        kml_path = cls._map_path_for_farm(farm_name, instance_path)
        if not kml_path.exists():
            return {"updated": False, "path": str(kml_path)}

        original_text = kml_path.read_text(encoding="utf-8")
        original_bytes = original_text.encode("utf-8")
        old_tag = f"<name>{escape(old_name)}</name>"
        new_tag = f"<name>{escape(new_name)}</name>"
        match_count = original_text.count(old_tag)
        if match_count == 0:
            raise ValueError("Unable to update the farm map because the paddock placemark was not found")
        if match_count > 1:
            raise ValueError("Unable to update the farm map because duplicate paddock placemark names were found")

        updated_text = original_text.replace(old_tag, new_tag, 1)
        temp_path = None
        try:
            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=kml_path.parent,
                suffix=".kml.tmp",
                delete=False,
            ) as temp_file:
                temp_file.write(updated_text)
                temp_path = Path(temp_file.name)
            temp_path.replace(kml_path)
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink(missing_ok=True)
        return {
            "updated": True,
            "path": str(kml_path),
            "original_bytes": original_bytes,
        }

    @classmethod
    def rename_paddock(
        cls,
        paddock: Paddock,
        new_name: str | None,
        *,
        instance_path: str | Path,
    ) -> dict:
        validated_name = cls.validate_available_name(
            str(paddock.farm_id),
            new_name,
            exclude_paddock_id=str(paddock.id),
        )
        if validated_name == paddock.name:
            return {"changed": False, "map_updated": False, "name": paddock.name}

        map_result = cls._rename_paddock_in_kml(
            paddock.farm.name,
            paddock.name,
            validated_name,
            instance_path=instance_path,
        )
        previous_name = paddock.name
        paddock.name = validated_name

        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            if map_result.get("updated"):
                Path(map_result["path"]).write_bytes(map_result["original_bytes"])
            paddock.name = previous_name
            raise

        return {
            "changed": True,
            "map_updated": map_result.get("updated", False),
            "map_path": map_result.get("path"),
            "name": paddock.name,
        }
