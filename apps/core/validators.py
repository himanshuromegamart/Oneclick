"""Reusable validators.

Input validation lives here rather than in serialisers so the same rule applies
whether a value arrives from the API, a management command or a Celery task.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath

from django.conf import settings

from apps.core.exceptions import ErrorCode, ValidationFailed

# Indian mobile numbers: 10 digits starting 6-9, optionally +91 prefixed.
_PHONE_RE = re.compile(r"^(?:\+?91)?([6-9]\d{9})$")

# Characters Windows/macOS reject in filenames, plus control characters. Names
# are shown as folders on a phone, so keeping them portable avoids surprises.
_ILLEGAL_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_WINDOWS_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)

MAX_NAME_LENGTH = 255


def normalize_phone_number(raw: str) -> str:
    """Return E.164 (``+91XXXXXXXXXX``) or raise.

    Normalising on the way in means the database holds exactly one
    representation, so ``0 98765 43210``, ``+919876543210`` and ``9876543210``
    all resolve to the same user - and the unique constraint actually works.
    """
    if not raw:
        raise ValidationFailed(
            detail="Mobile number is required.", details={"field": "phone_number"}
        )

    cleaned = re.sub(r"[\s\-()]", "", str(raw).strip())
    if cleaned.startswith("00"):
        cleaned = f"+{cleaned[2:]}"
    if cleaned.startswith("0"):
        cleaned = cleaned[1:]

    match = _PHONE_RE.match(cleaned)
    if not match:
        raise ValidationFailed(
            detail="Enter a valid 10-digit Indian mobile number.",
            details={"field": "phone_number"},
        )
    return f"+91{match.group(1)}"


def validate_node_name(name: str, kind: str = "folder") -> str:
    """Validate and normalise a folder or file name."""
    if name is None or not str(name).strip():
        raise ValidationFailed(
            detail=f"{kind.title()} name is required.", details={"field": "name"}
        )

    # NFC keeps visually identical Unicode from producing two distinct rows
    # that both satisfy the unique constraint.
    cleaned = unicodedata.normalize("NFC", str(name).strip())

    if len(cleaned) > MAX_NAME_LENGTH:
        raise ValidationFailed(
            detail=f"{kind.title()} name cannot exceed {MAX_NAME_LENGTH} characters.",
            details={"field": "name", "max_length": MAX_NAME_LENGTH},
        )
    if _ILLEGAL_NAME_CHARS.search(cleaned):
        raise ValidationFailed(
            detail=r'A name cannot contain < > : " / \ | ? * or control characters.',
            details={"field": "name"},
        )
    if cleaned in {".", ".."}:
        raise ValidationFailed(detail="Invalid name.", details={"field": "name"})
    if cleaned.rstrip(". ") != cleaned:
        raise ValidationFailed(
            detail="A name cannot end with a space or a dot.",
            details={"field": "name"},
        )
    if PurePosixPath(cleaned).stem.upper() in _RESERVED_WINDOWS_NAMES:
        raise ValidationFailed(detail=f"{cleaned!r} is a reserved name.", details={"field": "name"})
    return cleaned


def extract_extension(filename: str) -> str:
    suffix = PurePosixPath(filename or "").suffix
    return suffix[1:].lower() if suffix else ""


def validate_upload(filename: str, size_bytes: int, content_type: str = "") -> tuple[str, str]:
    """Validate an upload's name, extension and size.

    Returns ``(safe_name, extension)``.

    The extension check is a *deny* list first, then an *allow* list.  Relying
    on ``content_type`` alone would be unsafe: it is client-supplied and
    trivially forged.
    """
    safe_name = validate_node_name(filename, kind="file")
    extension = extract_extension(safe_name)
    storage = settings.STORAGE_SETTINGS

    if not extension:
        raise ValidationFailed(
            detail="The file must have an extension.",
            code=ErrorCode.FILE_TYPE_BLOCKED,
            details={"field": "file"},
        )
    if extension in {e.lower() for e in storage["BLOCKED_EXTENSIONS"]}:
        raise ValidationFailed(
            detail=f".{extension} files are not allowed.",
            code=ErrorCode.FILE_TYPE_BLOCKED,
            details={"extension": extension},
        )
    allowed = {e.lower() for e in storage["ALLOWED_EXTENSIONS"]}
    if allowed and extension not in allowed:
        raise ValidationFailed(
            detail=f".{extension} files are not supported.",
            code=ErrorCode.FILE_TYPE_BLOCKED,
            details={"extension": extension, "allowed": sorted(allowed)},
        )

    max_bytes = storage["MAX_UPLOAD_BYTES"]
    if size_bytes is None or size_bytes <= 0:
        raise ValidationFailed(detail="The file is empty.", details={"field": "file"})
    if size_bytes > max_bytes:
        raise ValidationFailed(
            detail=f"The file exceeds the {max_bytes // (1024 * 1024)} MB limit.",
            code=ErrorCode.FILE_TOO_LARGE,
            details={"size_bytes": size_bytes, "max_bytes": max_bytes},
        )
    return safe_name, extension


def validate_tags(tags: list[str] | None) -> list[str]:
    """Normalise tags to lowercase, de-duplicated, length-capped."""
    if not tags:
        return []
    if len(tags) > 25:
        raise ValidationFailed(detail="A maximum of 25 tags is allowed.", details={"field": "tags"})
    seen: list[str] = []
    for tag in tags:
        cleaned = re.sub(r"\s+", " ", str(tag).strip().lower())
        if not cleaned:
            continue
        if len(cleaned) > 50:
            raise ValidationFailed(
                detail="A tag cannot exceed 50 characters.", details={"field": "tags", "tag": tag}
            )
        if cleaned not in seen:
            seen.append(cleaned)
    return seen
