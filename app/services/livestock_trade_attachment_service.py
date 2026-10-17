"""Reference images for livestock imports, staged separately from committed trades.

Draft tokens expire after 24 hours. A staging request lazily removes up to 200
expired draft files; inactive installations may retain them until another upload.
Stored files are immutable, and staged originals survive a failed save or retry.
Format signatures and basic structure are checked; images are never decoded.
"""

import hashlib
import hmac
import os
import time
from pathlib import Path
from uuid import UUID, uuid4

from flask import current_app
from itsdangerous import BadData, SignatureExpired, URLSafeTimedSerializer
from werkzeug.utils import secure_filename

from app.models import LivestockTradeAttachment


class LivestockTradeAttachmentService:
    MAX_ATTACHMENT_BYTES = 12 * 1024 * 1024
    MAX_ATTACHMENTS = 10
    DRAFT_TTL_SECONDS = 24 * 60 * 60
    CLEANUP_SCAN_LIMIT = 200
    STAGING_ROOT = "livestock_trade_drafts"
    ATTACHMENT_ROOT = "livestock_trade_attachments"
    SUFFIXES = {
        "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
        "image/heic": ".heic", "image/heif": ".heif",
    }

    @staticmethod
    def _serializer():
        return URLSafeTimedSerializer(current_app.secret_key, salt="livestock-trade-image-v1")

    @classmethod
    def _root(cls, name):
        return (Path(current_app.instance_path) / name).resolve()

    @staticmethod
    def _valid_uuid(value):
        try:
            return isinstance(value, str) and str(UUID(value)) == value
        except (ValueError, AttributeError):
            return False

    @classmethod
    def _validate_descriptor(cls, descriptor):
        if not isinstance(descriptor, dict):
            raise ValueError("Invalid reference image token")
        filename = descriptor.get("original_filename")
        digest = descriptor.get("sha256")
        size = descriptor.get("byte_size")
        if (not cls._valid_uuid(descriptor.get("id"))
                or not isinstance(filename, str) or not filename or len(filename) > 255
                or secure_filename(filename) != filename
                or not isinstance(descriptor.get("content_type"), str)
                or descriptor["content_type"] not in cls.SUFFIXES
                or not isinstance(size, int) or isinstance(size, bool)
                or not 0 < size <= cls.MAX_ATTACHMENT_BYTES
                or not isinstance(digest, str) or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)
                or not isinstance(descriptor.get("owner"), str) or not descriptor["owner"]):
            raise ValueError("Invalid reference image token")

    @classmethod
    def _decode_token(cls, token, owner, *, allow_expired=False):
        if not isinstance(token, str) or len(token) > 4096:
            raise ValueError("Invalid reference image token")
        try:
            descriptor = cls._serializer().loads(
                token, max_age=None if allow_expired else cls.DRAFT_TTL_SECONDS,
            )
        except SignatureExpired as exc:
            raise ValueError("Reference images expired after 24 hours. Please attach them again.") from exc
        except BadData as exc:
            raise ValueError("Invalid reference image token") from exc
        cls._validate_descriptor(descriptor)
        if not isinstance(owner, str) or not hmac.compare_digest(descriptor["owner"].encode(), owner.encode()):
            raise ValueError("Reference images belong to a different browser session. Please attach them again.")
        return {**descriptor, "token": token}

    @classmethod
    def staged_path(cls, descriptor):
        if not cls._valid_uuid(descriptor.get("id")):
            raise ValueError("Invalid reference image identifier")
        root = cls._root(cls.STAGING_ROOT)
        path = (root / descriptor["id"]).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Invalid reference image path")
        return path

    @classmethod
    def _verify_staged_file(cls, descriptor):
        try:
            with cls.staged_path(descriptor).open("rb") as source:
                data = source.read(cls.MAX_ATTACHMENT_BYTES + 1)
        except FileNotFoundError as exc:
            raise ValueError("A reference image is no longer available. Please attach it again.") from exc
        if (len(data) != descriptor["byte_size"]
                or not hmac.compare_digest(hashlib.sha256(data).hexdigest(), descriptor["sha256"])):
            raise ValueError("A reference image changed. Please attach it again.")
        return data

    @classmethod
    def describe_token(cls, token, owner):
        descriptor = cls._decode_token(token, owner)
        cls._verify_staged_file(descriptor)
        return descriptor

    @classmethod
    def resolve_tokens(cls, tokens, owner):
        tokens = list(tokens)
        if len(tokens) > cls.MAX_ATTACHMENTS:
            raise ValueError(f"Attach up to {cls.MAX_ATTACHMENTS} reference images per trade")
        descriptors = [cls.describe_token(token, owner) for token in tokens]
        if len({item["id"] for item in descriptors}) != len(descriptors):
            raise ValueError("The same reference image was included more than once")
        return descriptors

    @staticmethod
    def _image_type(data):
        if len(data) >= 12 and data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9"):
            return "image/jpeg"
        if (len(data) >= 45 and data.startswith(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
                and data.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82")
                and int.from_bytes(data[16:20], "big") > 0 and int.from_bytes(data[20:24], "big") > 0):
            return "image/png"
        if (len(data) >= 20 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
                and data[12:16] in (b"VP8 ", b"VP8L", b"VP8X")
                and int.from_bytes(data[4:8], "little") + 8 == len(data)
                and int.from_bytes(data[16:20], "little") <= len(data) - 20):
            return "image/webp"
        if len(data) >= 16 and data[4:8] == b"ftyp":
            box_size = int.from_bytes(data[:4], "big")
            if 16 <= box_size <= min(len(data), 4096) and box_size % 4 == 0:
                brands = {data[8:12], *(data[index:index + 4] for index in range(16, box_size, 4))}
                if brands.intersection({b"avif", b"avis"}):
                    return None
                if brands.intersection({b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs"}):
                    return "image/heic"
                if brands.intersection({b"mif1", b"msf1"}):
                    return "image/heif"
        return None

    @classmethod
    def stage_uploads(cls, uploads, owner, existing=()):
        if not isinstance(owner, str) or not owner:
            raise ValueError("A browser session is required to attach reference images")
        retained = cls.resolve_tokens(
            [item["token"] if isinstance(item, dict) else item for item in existing], owner,
        )
        uploads = [upload for upload in uploads if upload is not None and upload.filename]
        if len(retained) + len(uploads) > cls.MAX_ATTACHMENTS:
            raise ValueError(f"Attach up to {cls.MAX_ATTACHMENTS} reference images per trade")
        cls.cleanup_expired_drafts()
        prepared = []
        for upload in uploads:
            data = upload.read(cls.MAX_ATTACHMENT_BYTES + 1)
            if not data:
                raise ValueError("Reference image is empty")
            if len(data) > cls.MAX_ATTACHMENT_BYTES:
                raise ValueError("Each reference image must be 12 MiB or smaller")
            content_type = cls._image_type(data)
            if content_type is None:
                raise ValueError("Reference images must be JPEG, PNG, WebP, HEIC or HEIF files")
            filename = secure_filename(upload.filename) or "reference-image"
            stem = Path(filename).stem[:220] or "reference-image"
            descriptor = {
                "id": str(uuid4()), "owner": owner,
                "original_filename": stem + cls.SUFFIXES[content_type],
                "content_type": content_type, "byte_size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            prepared.append((descriptor, data))
        created = []
        try:
            for descriptor, data in prepared:
                target = cls.staged_path(descriptor)
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as destination:
                    # Only clean up files this attempt actually created.
                    created.append(descriptor)
                    destination.write(data)
                descriptor["token"] = cls._serializer().dumps(descriptor)
            return retained + created
        except Exception:
            for descriptor in created:
                cls._unlink(cls.staged_path(descriptor))
            raise

    @classmethod
    def cleanup_expired_drafts(cls):
        root = cls._root(cls.STAGING_ROOT)
        if not root.exists():
            return
        cutoff = time.time() - cls.DRAFT_TTL_SECONDS
        with os.scandir(root) as entries:
            for index, entry in enumerate(entries):
                if index >= cls.CLEANUP_SCAN_LIMIT:
                    break
                try:
                    if (cls._valid_uuid(entry.name) and entry.is_file(follow_symlinks=False)
                            and entry.stat(follow_symlinks=False).st_mtime < cutoff):
                        cls._unlink(Path(entry.path))
                except FileNotFoundError:
                    continue

    @classmethod
    def discard_tokens(cls, tokens, owner):
        # Expiration prevents reuse, not removal. Validate the whole batch before deleting.
        tokens = list(tokens)
        if len(tokens) > cls.MAX_ATTACHMENTS:
            raise ValueError(f"Attach up to {cls.MAX_ATTACHMENTS} reference images per trade")
        descriptors = [cls._decode_token(token, owner, allow_expired=True) for token in tokens]
        for descriptor in descriptors:
            cls._unlink(cls.staged_path(descriptor))

    @staticmethod
    def fingerprints(attachments):
        return [{key: item[key] for key in ("sha256", "original_filename", "content_type", "byte_size")} for item in attachments]

    @classmethod
    def permanent_path(cls, attachment_or_path):
        relative = Path(getattr(attachment_or_path, "storage_path", attachment_or_path))
        root = cls._root(cls.ATTACHMENT_ROOT)
        path = (Path(current_app.instance_path) / relative).resolve()
        if relative.is_absolute() or not path.is_relative_to(root) or path == root:
            raise ValueError("Invalid reference image path")
        return path

    @classmethod
    def copy_to_trade(cls, trade, descriptors):
        """Prepare immutable files/rows; the caller must commit or remove all files."""
        descriptors = list(descriptors)
        if not descriptors:
            return []
        if len(descriptors) > cls.MAX_ATTACHMENTS:
            raise ValueError(f"Attach up to {cls.MAX_ATTACHMENTS} reference images per trade")
        created = []
        try:
            for index, source in enumerate(descriptors):
                descriptor = cls._decode_token(source["token"], source["owner"])
                # Verify the exact bytes copied, without a second filesystem read.
                data = cls._verify_staged_file(descriptor)
                attachment_id = str(uuid4())
                relative = Path(cls.ATTACHMENT_ROOT) / str(trade.id) / (attachment_id + cls.SUFFIXES[descriptor["content_type"]])
                attachment = LivestockTradeAttachment(
                    id=attachment_id, sort_order=index, storage_path=relative.as_posix(),
                    **{key: descriptor[key] for key in ("original_filename", "content_type", "byte_size", "sha256")},
                )
                target = cls.permanent_path(attachment)
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as destination:
                    created.append(attachment)
                    destination.write(data)
            trade.attachments.extend(created)
            return created
        except Exception:
            cls.delete_files(created)
            raise

    @staticmethod
    def _unlink(path):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            current_app.logger.exception("Could not remove livestock trade reference image %s", path)

    @classmethod
    def delete_files(cls, attachments):
        """Best-effort cleanup after a committed delete or a rolled-back create."""
        for attachment in attachments:
            try:
                cls._unlink(cls.permanent_path(attachment))
            except ValueError:
                current_app.logger.exception("Invalid livestock trade reference image storage path")
