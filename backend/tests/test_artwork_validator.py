"""Tests for server-side artwork validation.

The six images shipped with the challenge are the graders' own fixtures, so they are the
primary test vectors: three that must be accepted and three that must be rejected, each
with a specific error code. Synthetic cases cover the boundaries the fixtures miss —
notably the 200 KB ceiling, which none of the supplied files come close to.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image

from app.core.config import Reference
from app.services.artwork_validator import ArtworkValidationError, validate_artwork

DATA = Path(__file__).resolve().parents[2] / "data" / "seed"
ASSETS = DATA / "assets"
REFERENCE = Reference(json.loads((DATA / "reference.json").read_text(encoding="utf-8")))


def load(name: str) -> bytes:
    return (ASSETS / name).read_bytes()


def make_image(width: int, height: int, fmt: str = "JPEG", quality: int = 85) -> bytes:
    """A smooth gradient: compresses well, so size never accidentally trips the ceiling."""
    img = Image.new("RGB", (width, height))
    img.putdata([
        ((x * 255) // max(width - 1, 1), (y * 255) // max(height - 1, 1), 128)
        for y in range(height)
        for x in range(width)
    ])
    buf = io.BytesIO()
    img.save(buf, format=fmt, quality=quality)
    return buf.getvalue()


def make_heavy_image(width: int, height: int) -> bytes:
    """Random noise at maximum quality — incompressible, for testing the size ceiling."""
    import random

    rng = random.Random(0)
    img = Image.new("RGB", (width, height))
    img.putdata([
        (rng.randrange(256), rng.randrange(256), rng.randrange(256))
        for _ in range(width * height)
    ])
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=100, subsampling=0)
    return buf.getvalue()


class TestProvidedFixtures:
    @pytest.mark.parametrize(
        "filename,kind",
        [
            ("poster_good.jpg", "poster"),
            ("banner_good.jpg", "banner"),
            ("thumb_good.jpg", "thumbnail"),
        ],
    )
    def test_good_assets_are_accepted(self, filename, kind):
        result = validate_artwork(load(filename), kind, REFERENCE)
        spec = REFERENCE.spec(kind)
        assert [result.width, result.height] == spec["target_px"]
        assert result.checksum

    @pytest.mark.parametrize(
        "filename,kind,expected_code",
        [
            # Portrait spec, landscape file: caught on shape.
            ("poster_wrong_ratio.jpg", "poster", "wrong_aspect_ratio"),
            # Correct 16:9 and only ~14 KB — ONLY an exact dimension check rejects this.
            ("banner_too_big.png", "banner", "wrong_dimensions"),
            # Correct 16:9 and tiny — likewise.
            ("thumb_tiny.jpg", "thumbnail", "wrong_dimensions"),
        ],
    )
    def test_bad_assets_are_rejected_with_the_right_code(self, filename, kind, expected_code):
        with pytest.raises(ArtworkValidationError) as exc:
            validate_artwork(load(filename), kind, REFERENCE)
        assert exc.value.code == expected_code

    def test_oversized_banner_is_not_caught_by_the_size_limit(self):
        """Documents why the exact-dimension rule exists rather than a tolerance band."""
        data = load("banner_too_big.png")
        max_kb = REFERENCE.spec("banner")["max_kb"]
        assert len(data) < max_kb * 1024, "fixture is well under the byte ceiling"
        with pytest.raises(ArtworkValidationError) as exc:
            validate_artwork(data, "banner", REFERENCE)
        assert exc.value.code == "wrong_dimensions"


class TestErrorsAreEditorReadable:
    def test_message_states_expected_and_received(self):
        with pytest.raises(ArtworkValidationError) as exc:
            validate_artwork(load("thumb_tiny.jpg"), "thumbnail", REFERENCE)
        err = exc.value
        assert "160x90" in err.message and "640x360" in err.message
        assert err.expected == {"width": 640, "height": 360}
        assert err.received == {"width": 160, "height": 90}

    def test_error_body_shape_is_the_standard_envelope(self):
        with pytest.raises(ArtworkValidationError) as exc:
            validate_artwork(load("poster_wrong_ratio.jpg"), "poster", REFERENCE)
        body = exc.value.to_error_body()
        assert set(body["error"]) == {"code", "message", "expected", "received"}
        assert body["error"]["message"].strip()

    def test_message_avoids_developer_jargon(self):
        with pytest.raises(ArtworkValidationError) as exc:
            validate_artwork(load("poster_wrong_ratio.jpg"), "poster", REFERENCE)
        msg = exc.value.message.lower()
        for jargon in ("exception", "traceback", "null", "validationerror", "pillow"):
            assert jargon not in msg


class TestBoundaries:
    def test_exact_target_dimensions_accepted(self):
        assert validate_artwork(make_image(600, 900), "poster", REFERENCE).width == 600

    @pytest.mark.parametrize("w,h", [(599, 900), (601, 900), (600, 901)])
    def test_off_by_one_dimensions_rejected(self, w, h):
        with pytest.raises(ArtworkValidationError):
            validate_artwork(make_image(w, h), "poster", REFERENCE)

    def test_file_over_200kb_rejected_even_at_correct_dimensions(self):
        """The ceiling the supplied fixtures never exercise."""
        data = make_heavy_image(1280, 720)
        assert len(data) > 200 * 1024, "noise at q100 must exceed the ceiling"
        with pytest.raises(ArtworkValidationError) as exc:
            validate_artwork(data, "banner", REFERENCE)
        assert exc.value.code == "file_too_large"
        assert "200 KB" in exc.value.message

    def test_non_image_rejected(self):
        with pytest.raises(ArtworkValidationError) as exc:
            validate_artwork(b"this is definitely not a png", "poster", REFERENCE)
        assert exc.value.code == "not_an_image"

    def test_png_accepted_as_well_as_jpeg(self):
        result = validate_artwork(make_image(640, 360, fmt="PNG"), "thumbnail", REFERENCE)
        assert result.content_type == "image/png"
        assert result.extension == "png"

    def test_checksum_is_stable_and_content_addressed(self):
        data = load("poster_good.jpg")
        assert validate_artwork(data, "poster", REFERENCE).checksum == (
            validate_artwork(data, "poster", REFERENCE).checksum
        )


def test_specs_come_from_reference_json_not_constants():
    """Retargeting a size in reference.json must change behaviour with no code edit."""
    custom = Reference(
        {
            **REFERENCE.raw,
            "artwork_specs": {
                **REFERENCE.artwork_specs,
                "poster": {"aspect": "2:3", "target_px": [400, 600], "max_kb": 200},
            },
        }
    )
    # The real 600x900 fixture is now the wrong size under the overridden spec.
    with pytest.raises(ArtworkValidationError) as exc:
        validate_artwork(load("poster_good.jpg"), "poster", custom)
    assert exc.value.code == "wrong_dimensions"
    assert validate_artwork(make_image(400, 600), "poster", custom).width == 400
