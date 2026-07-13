import hashlib
from pathlib import Path

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import ShearingSession, ShearingSessionAttachment
from app.services.task_service import TaskService


class ShearingAttachmentService:
    ATTACHMENT_ROOT = "shearing_attachments"
    MAX_ATTACHMENT_BYTES = 24 * 1024 * 1024
    ALLOWED_ATTACHMENT_TYPES = {
        "application/msword",
        "application/pdf",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "image/heic",
        "image/heif",
        "image/jpeg",
        "image/png",
        "image/webp",
        "text/csv",
        "text/plain",
    }
    ALLOWED_SUFFIXES = {
        ".csv",
        ".doc",
        ".docx",
        ".heic",
        ".heif",
        ".jpeg",
        ".jpg",
        ".pdf",
        ".png",
        ".txt",
        ".webp",
        ".xls",
        ".xlsx",
    }
    DEFAULT_SUFFIX_BY_TYPE = {
        "application/msword": ".doc",
        "application/pdf": ".pdf",
        "application/vnd.ms-excel": ".xls",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "image/heic": ".heic",
        "image/heif": ".heif",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "text/csv": ".csv",
        "text/plain": ".txt",
    }

    @classmethod
    def create_attachments_from_uploads(
        cls,
        uploads: list[FileStorage],
        *,
        session: ShearingSession,
        instance_path: str | Path,
        uploaded_by_user_id: str | None = None,
        caption: str | None = None,
    ) -> list[ShearingSessionAttachment]:
        created = []
        for upload in uploads:
            if upload is None or not upload.filename:
                continue
            created.append(
                cls.store_upload(
                    upload,
                    session=session,
                    instance_path=instance_path,
                    uploaded_by_user_id=uploaded_by_user_id,
                    caption=caption,
                )
            )
        return created

    @classmethod
    def store_upload(
        cls,
        upload: FileStorage,
        *,
        session: ShearingSession,
        instance_path: str | Path,
        uploaded_by_user_id: str | None = None,
        caption: str | None = None,
    ) -> ShearingSessionAttachment:
        original_filename = secure_filename(upload.filename or "") or "shearing-attachment"
        content_type = (upload.mimetype or "application/octet-stream").lower()
        if content_type not in cls.ALLOWED_ATTACHMENT_TYPES:
            raise ValueError("Attachment file type is not supported")

        data = upload.read()
        if not data:
            raise ValueError("Attachment file is empty")
        if len(data) > cls.MAX_ATTACHMENT_BYTES:
            raise ValueError("Attachment file is too large")

        attachment = ShearingSessionAttachment(
            session_id=session.id,
            uploaded_by_user_id=uploaded_by_user_id,
            original_filename=original_filename[:255],
            content_type=content_type,
            byte_size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            caption=TaskService.optional_text(caption, TaskService.MAX_DESCRIPTION_LENGTH),
            storage_path="",
        )
        db.session.add(attachment)
        db.session.flush()

        relative_path = cls.attachment_storage_path(
            session_id=str(session.id),
            attachment_id=str(attachment.id),
            filename=original_filename,
            content_type=content_type,
        )
        target = cls.absolute_attachment_path(instance_path, relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        attachment.storage_path = relative_path.as_posix()
        db.session.flush()
        return attachment

    @classmethod
    def attachment_storage_path(
        cls,
        *,
        session_id: str,
        attachment_id: str,
        filename: str,
        content_type: str,
    ) -> Path:
        suffix = Path(filename).suffix.lower()
        if suffix not in cls.ALLOWED_SUFFIXES:
            suffix = cls.DEFAULT_SUFFIX_BY_TYPE.get(content_type, ".bin")
        return Path(cls.ATTACHMENT_ROOT) / str(session_id) / f"{attachment_id}{suffix}"

    @staticmethod
    def absolute_attachment_path(instance_path: str | Path, relative_path: str | Path) -> Path:
        root = Path(instance_path).resolve()
        target = (root / relative_path).resolve()
        if not target.is_relative_to(root):
            raise FileNotFoundError(relative_path)
        return target

    @classmethod
    def delete_attachment(cls, attachment: ShearingSessionAttachment, *, instance_path: str | Path) -> None:
        try:
            target = cls.absolute_attachment_path(instance_path, attachment.storage_path)
        except FileNotFoundError:
            target = None
        db.session.delete(attachment)
        if target is not None and target.exists():
            target.unlink()
