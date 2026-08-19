"""Server-side artwork validation.

Every rule here is enforced on the server. The CMS mirrors some of them for instant
feedback, but the client's opinion is never trusted — an upload that bypasses the CMS
entirely still gets the same treatment.

Rules come from `reference.json`'s `artwork_specs`, never from constants in this file.

**Why dimensions must match `target_px` exactly** (see DECISIONS D-002): the provided
fixtures include `banner_too_big.png` at 2560x1440 and `thumb_tiny.jpg` at 160x90. Both
have a *correct* 16:9 aspect ratio and both are far under the 200 KB ceiling, so neither
a ratio check nor a size check rejects them. Only an exact dimension check does. A
tolerance band of "up to 2x target" — which is tempting, and which an earlier draft of
the plan called for — would have accepted `banner_too_big` exactly.

Error messages are written for a content editor: what is wrong, what was expected, what
was received, and what to do about it. The CMS renders `message` verbatim.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

from app.core.config import Reference

# Pillow format -> the content type and extension we store it as.
_ALLOWED_FORMATS = {
    "JPEG": ("image/jpeg", "jpg"),
    "PNG": ("image/png", "png"),
}


class ArtworkValidationError(Exception):
    """Raised when an upload fails a rule. Carries an editor-readable payload."""

    def __init__(self, code: str, message: str, expected: dict | None = None,
                 received: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.expected = expected or {}
        self.received = received or {}

    def to_error_body(self) -> dict:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "expected": self.expected,
                "received": self.received,
            }
        }


@dataclass(frozen=True)
class ValidatedImage:
    width: int
    height: int
    size_bytes: int
    content_type: str
    extension: str
    checksum: str


def _fmt_ratio(w: int, h: int) -> str:
    """Render a ratio the way an editor thinks about it, e.g. '16:9'."""
    from math import gcd

    g = gcd(w, h) or 1
    return f"{w // g}:{h // g}"


def _kb(n: int) -> str:
    return f"{n / 1024:.0f} KB"


def validate_artwork(data: bytes, kind: str, reference: Reference) -> ValidatedImage:
    """Validate raw upload bytes against the spec for `kind`.

    Order matters, because it decides which message the editor sees when an image is
    wrong in more than one way. We report the most fundamental problem first:
    not-an-image, then too-large-to-store, then wrong shape, then wrong size.
    """
    spec = reference.spec(kind)
    target_w, target_h = spec["target_px"]
    max_bytes = int(spec["max_kb"]) * 1024

    # 1. Is it an image at all? `verify()` parses the container without decoding pixels.
    try:
        probe = Image.open(io.BytesIO(data))
        probe.verify()
        fmt = probe.format
    except (UnidentifiedImageError, OSError, ValueError):
        raise ArtworkValidationError(
            code="not_an_image",
            message=(
                "That file isn't an image we can read. Please upload a JPG or PNG "
                "exported from your image editor."
            ),
            expected={"formats": ["JPG", "PNG"]},
        ) from None

    if fmt not in _ALLOWED_FORMATS:
        raise ArtworkValidationError(
            code="unsupported_format",
            message=(
                f"{fmt} images aren't supported. Please re-export this picture as a "
                "JPG or a PNG and upload it again."
            ),
            expected={"formats": ["JPG", "PNG"]},
            received={"format": fmt},
        )

    content_type, extension = _ALLOWED_FORMATS[fmt]

    # 2. File size ceiling, checked on the raw bytes before decoding pixels.
    if len(data) > max_bytes:
        raise ArtworkValidationError(
            code="file_too_large",
            message=(
                f"This image is {_kb(len(data))}, but the limit for a {kind} is "
                f"{spec['max_kb']} KB. Try exporting it as a JPG at around 80% quality "
                "— that usually cuts the size by more than half without a visible change."
            ),
            expected={"max_kb": spec["max_kb"]},
            received={"size_kb": round(len(data) / 1024, 1)},
        )

    # Re-open, because verify() leaves the file object unusable for further reads.
    with Image.open(io.BytesIO(data)) as img:
        width, height = img.size

    if width <= 0 or height <= 0:
        raise ArtworkValidationError(
            code="not_an_image",
            message="That image reports a zero width or height and can't be used.",
            received={"width": width, "height": height},
        )

    # 3. Aspect ratio. Checked before exact dimensions so that a portrait image
    #    uploaded into a landscape slot is told it is the wrong *shape* — the useful
    #    message — rather than merely the wrong size.
    target_ratio = target_w / target_h
    actual_ratio = width / height
    if abs(actual_ratio - target_ratio) > 0.01 * target_ratio:
        raise ArtworkValidationError(
            code="wrong_aspect_ratio",
            message=(
                f"This {kind} is the wrong shape. It should be "
                f"{_fmt_ratio(target_w, target_h)} ({target_w}x{target_h} pixels), but "
                f"this image is {_fmt_ratio(width, height)} ({width}x{height}). "
                "Crop it to the required shape rather than stretching it."
            ),
            expected={
                "aspect": spec["aspect"],
                "width": target_w,
                "height": target_h,
            },
            received={
                "aspect": _fmt_ratio(width, height),
                "width": width,
                "height": height,
            },
        )

    # 4. Exact dimensions.
    if (width, height) != (target_w, target_h):
        direction = "larger" if width > target_w else "smaller"
        raise ArtworkValidationError(
            code="wrong_dimensions",
            message=(
                f"This {kind} is {width}x{height} pixels, which is {direction} than the "
                f"required {target_w}x{target_h}. The shape is right, so resizing it to "
                f"exactly {target_w}x{target_h} will work."
            ),
            expected={"width": target_w, "height": target_h},
            received={"width": width, "height": height},
        )

    return ValidatedImage(
        width=width,
        height=height,
        size_bytes=len(data),
        content_type=content_type,
        extension=extension,
        checksum=hashlib.sha256(data).hexdigest(),
    )
