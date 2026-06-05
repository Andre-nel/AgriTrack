import hashlib
import uuid
from pathlib import Path

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import NoteAttachment
from app.services.task_service import TaskService


class NoteAttachmentService:
    ATTACHMENT_ROOT = "note_attachments"
    MAX_ATTACHMENT_BYTES = 12 * 1024 * 1024
    ALLOWED_ATTACHMENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
    IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
    EVENT_ID_FIELDS = {
        "mob_event": "mob_event_id",
        "paddock_event": "paddock_event_id",
        "water_asset_event": "water_asset_event_id",
    }

    @classmethod
    def create_attachments_from_uploads(
        cls,
        uploads: list[FileStorage],
        *,
        farm_id: str,
        event_type: str,
        event_id: str,
        instance_path: str | Path,
        uploaded_by_user_id: str | None = None,
        caption: str | None = None,
        captured_at=None,
    ) -> list[NoteAttachment]:
        created = []
        for upload in uploads:
            if upload is None or not upload.filename:
                continue
            attachment, _duplicate = cls.store_upload(
                upload,
                farm_id=farm_id,
                event_type=event_type,
                event_id=event_id,
                instance_path=instance_path,
                uploaded_by_user_id=uploaded_by_user_id,
                caption=caption,
                captured_at=captured_at,
            )
            created.append(attachment)
        return created

    @classmethod
    def store_upload(
        cls,
        upload: FileStorage,
        *,
        farm_id: str,
        event_type: str,
        event_id: str,
        instance_path: str | Path,
        uploaded_by_user_id: str | None = None,
        client_attachment_id: str | None = None,
        caption: str | None = None,
        captured_at=None,
        expected_sha256: str | None = None,
    ) -> tuple[NoteAttachment, bool]:
        if event_type not in cls.EVENT_ID_FIELDS:
            raise ValueError("Attachment event type is invalid")
        normalized_client_id = (client_attachment_id or str(uuid.uuid4())).strip()
        if not normalized_client_id:
            raise ValueError("client_attachment_id is required")

        existing = cls._existing_user_attachment(uploaded_by_user_id, normalized_client_id)
        if existing is not None:
            if cls._event_id_for(existing, event_type) != str(event_id):
                raise ValueError("client_attachment_id already belongs to another note")
            return existing, True

        original_filename = secure_filename(upload.filename or "") or "note-photo.jpg"
        content_type = (upload.mimetype or "application/octet-stream").lower()
        if content_type not in cls.ALLOWED_ATTACHMENT_TYPES:
            raise ValueError("Only image attachments are supported")

        data = upload.read()
        if not data:
            raise ValueError("Attachment file is empty")
        if len(data) > cls.MAX_ATTACHMENT_BYTES:
            raise ValueError("Attachment file is too large")

        digest = hashlib.sha256(data).hexdigest()
        requested_digest = (expected_sha256 or "").strip().lower()
        if requested_digest and requested_digest != digest:
            raise ValueError("Attachment checksum does not match")

        attachment_kwargs = {
            "farm_id": farm_id,
            cls.EVENT_ID_FIELDS[event_type]: event_id,
            "uploaded_by_user_id": uploaded_by_user_id,
            "client_attachment_id": normalized_client_id[:120],
            "original_filename": original_filename[:255],
            "content_type": content_type,
            "byte_size": len(data),
            "sha256": digest,
            "caption": TaskService.optional_text(caption, TaskService.MAX_DESCRIPTION_LENGTH),
            "captured_at": captured_at,
            "storage_path": "",
        }
        attachment = NoteAttachment(**attachment_kwargs)
        db.session.add(attachment)
        db.session.flush()

        relative_path = cls.attachment_storage_path(
            farm_id=farm_id,
            event_type=event_type,
            event_id=event_id,
            attachment_id=str(attachment.id),
            filename=original_filename,
        )
        target = cls.absolute_attachment_path(instance_path, relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        attachment.storage_path = relative_path.as_posix()
        db.session.flush()
        return attachment, False

    @classmethod
    def attachment_storage_path(
        cls,
        *,
        farm_id: str,
        event_type: str,
        event_id: str,
        attachment_id: str,
        filename: str,
    ) -> Path:
        suffix = Path(filename).suffix.lower()
        if suffix not in cls.IMAGE_SUFFIXES:
            suffix = ".jpg"
        return Path(cls.ATTACHMENT_ROOT) / str(farm_id) / event_type / f"{attachment_id}{suffix}"

    @staticmethod
    def absolute_attachment_path(instance_path: str | Path, relative_path: str | Path) -> Path:
        root = Path(instance_path).resolve()
        target = (root / relative_path).resolve()
        if not target.is_relative_to(root):
            raise FileNotFoundError(relative_path)
        return target

    @classmethod
    def event_type_for_attachment(cls, attachment: NoteAttachment) -> str:
        if attachment.mob_event_id:
            return "mob_event"
        if attachment.paddock_event_id:
            return "paddock_event"
        return "water_asset_event"

    @classmethod
    def _existing_user_attachment(
        cls,
        uploaded_by_user_id: str | None,
        client_attachment_id: str,
    ) -> NoteAttachment | None:
        if not uploaded_by_user_id:
            return None
        return NoteAttachment.query.filter_by(
            uploaded_by_user_id=uploaded_by_user_id,
            client_attachment_id=client_attachment_id,
        ).first()

    @classmethod
    def _event_id_for(cls, attachment: NoteAttachment, event_type: str) -> str | None:
        field_name = cls.EVENT_ID_FIELDS[event_type]
        value = getattr(attachment, field_name)
        return str(value) if value else None
