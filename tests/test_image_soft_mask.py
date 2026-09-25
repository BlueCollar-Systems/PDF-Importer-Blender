"""Synthetic PDF evidence for independently sized non-interpolated masks."""
from pathlib import Path
import sys
import types

import pymupdf as fitz
import pytest

sys.modules.setdefault("bpy", types.SimpleNamespace(
    app=types.SimpleNamespace(version=(4, 1, 0)),
    types=types.SimpleNamespace(Collection=object, Object=object),
))
sys.modules.setdefault("bmesh", types.SimpleNamespace())

from pdf_vector_importer import bl_import_engine
from pdf_vector_importer.image_soft_mask import (
    _non_interpolated, combine_image_soft_mask, common_sample_grid, repeat_sample_grid,
)


def image_document(*, image_size=(2, 1), mask_size=(3, 1),
                   color=bytes([255, 0, 0, 0, 0, 255]), mask=bytes([0, 128, 255]),
                   image_interpolate=None, mask_interpolate=None, decode=None, matte=None,
                   colorspace=fitz.csRGB):
    doc = fitz.open()
    page = doc.new_page(width=100, height=100)
    image = fitz.Pixmap(colorspace, *image_size, color, False)
    xref = page.insert_image(fitz.Rect(10, 20, 70, 50), pixmap=image, keep_proportion=False)
    mask_xref = doc.get_new_xref()
    doc.update_object(mask_xref, (
        "<< /Type /XObject /Subtype /Image /ColorSpace /DeviceGray "
        f"/Width {mask_size[0]} /Height {mask_size[1]} /BitsPerComponent 8 >>"
    ))
    doc.update_stream(mask_xref, mask)
    doc.xref_set_key(xref, "SMask", f"{mask_xref} 0 R")
    for target, key, value in [(xref, "Interpolate", image_interpolate),
                               (mask_xref, "Interpolate", mask_interpolate),
                               (mask_xref, "Decode", decode), (mask_xref, "Matte", matte)]:
        if value is not None:
            doc.xref_set_key(target, key, value)
    return doc, xref, mask_xref


def compose(doc, xref, mask_xref):
    return combine_image_soft_mask(fitz, doc, xref, mask_xref,
                                   fitz.Pixmap(doc, xref), fitz.Pixmap(doc, mask_xref))


def test_coprime_color_and_partial_alpha_boundaries_are_exact():
    with image_document()[0] as doc:
        xref, mask_xref = doc[0].get_images()[0][:2]
        with pytest.raises(Exception, match="same size"):
            fitz.Pixmap(fitz.Pixmap(doc, xref), fitz.Pixmap(doc, mask_xref))
        pix = compose(doc, xref, mask_xref)
        assert (pix.width, pix.height, pix.alpha) == (6, 1, 1)
        assert [pix.pixel(x, 0) for x in range(6)] == [
            (0, 0, 0, 0), (0, 0, 0, 0), (128, 0, 0, 128),
            (0, 0, 128, 128), (0, 0, 255, 255), (0, 0, 255, 255),
        ]
        reread = fitz.Pixmap(pix.tobytes("png"))
        assert reread.samples == pix.samples


def test_opposite_axis_resolutions_keep_every_cell_and_edge():
    doc, xref, mask_xref = image_document(
        image_size=(2, 3), mask_size=(3, 2),
        color=bytes([255, 0, 0, 0, 0, 255] * 3), mask=bytes([0, 128, 255, 255, 128, 0]),
    )
    with doc:
        pix = compose(doc, xref, mask_xref)
        assert (pix.width, pix.height) == (6, 6)
        assert [pix.pixel(x, 0)[3] for x in range(6)] == [0, 0, 128, 128, 255, 255]
        assert [pix.pixel(x, 5)[3] for x in range(6)] == [255, 255, 128, 128, 0, 0]
        assert pix.pixel(2, 2) == (128, 0, 0, 128)
        assert pix.pixel(3, 3) == (0, 0, 128, 128)


def test_inverted_decode_is_applied_once():
    doc, xref, mask_xref = image_document(decode="[1 0]")
    with doc:
        assert list(fitz.Pixmap(doc, mask_xref).samples) == [255, 127, 0]
        pix = compose(doc, xref, mask_xref)
        assert [pix.pixel(x, 0)[3] for x in range(6)] == [255, 255, 127, 127, 0, 0]


@pytest.mark.parametrize("colorspace,color", [
    (fitz.csGRAY, bytes([255, 0])),
    (fitz.csCMYK, bytes([0, 255, 255, 0, 255, 0, 0, 0])),
])
def test_original_color_conversion_and_alpha_survive(colorspace, color, tmp_path):
    doc, xref, _mask_xref = image_document(colorspace=colorspace, color=color)
    with doc:
        original_rgb = fitz.Pixmap(fitz.csRGB, fitz.Pixmap(doc, xref))
        placements = bl_import_engine._extract_image_placements(
            doc, doc[0], 1, types.SimpleNamespace(flip_y=True, user_scale=1.0), str(tmp_path),
        )
        pix = fitz.Pixmap(placements[0]["path"])
        assert pix.pixel(5, 0) == original_rgb.pixel(1, 0) + (255,)
        assert pix.pixel(0, 0) == (0, 0, 0, 0)


@pytest.mark.parametrize("options", [
    {"image_interpolate": "true"}, {"mask_interpolate": "true"},
    {"image_interpolate": "/Invalid"}, {"matte": "[1 1 1]"}, {"matte": "[]"},
])
def test_unproven_or_invalid_mismatch_is_loud_without_opaque_fallback(options, tmp_path):
    doc, _xref, _mask_xref = image_document(**options)
    with doc, pytest.raises(bl_import_engine.EmbeddedImageDeliveryError, match="page 1 image xref"):
        bl_import_engine._extract_image_placements(
            doc, doc[0], 1, types.SimpleNamespace(flip_y=True, user_scale=1.0), str(tmp_path),
        )
    assert not list(tmp_path.glob("*.png"))


def test_equal_size_path_keeps_existing_interpolation_behavior():
    doc, xref, mask_xref = image_document(mask_size=(2, 1), mask=bytes([128, 255]),
                                          image_interpolate="true", mask_interpolate="true")
    with doc:
        expected = fitz.Pixmap(fitz.Pixmap(doc, xref), fitz.Pixmap(doc, mask_xref))
        actual = compose(doc, xref, mask_xref)
        assert actual.samples == expected.samples
        assert (actual.width, actual.height) == (2, 1)


@pytest.mark.parametrize("value,allowed", [("false", True), ("true", False)])
def test_indirect_interpolate_boolean(value, allowed):
    doc, xref, mask_xref = image_document()
    with doc:
        reference = doc.get_new_xref()
        doc.update_object(reference, value)
        doc.xref_set_key(mask_xref, "Interpolate", f"{reference} 0 R")
        if allowed:
            assert compose(doc, xref, mask_xref).width == 6
        else:
            with pytest.raises(ValueError, match="interpolated"):
                compose(doc, xref, mask_xref)


def test_cyclic_interpolate_reference_fails_closed():
    doc, xref, mask_xref = image_document()
    with doc:
        reference = doc.get_new_xref()
        doc.update_object(reference, f"{reference} 0 R")
        doc.xref_set_key(mask_xref, "Interpolate", f"{reference} 0 R")
        with pytest.raises(ValueError, match="cyclic|invalid"):
            compose(doc, xref, mask_xref)


@pytest.mark.parametrize("cyclic", [True, False])
def test_interpolate_reference_cycle_and_depth_guards(cyclic):
    class ReferenceChain:
        def xref_get_key(self, _xref, _key):
            return "xref", "1 0 R"

        def xref_object(self, xref, **_kwargs):
            return "1 0 R" if cyclic else f"{xref + 1} 0 R"

    with pytest.raises(ValueError, match="cyclic|invalid"):
        _non_interpolated(ReferenceChain(), 50)


def test_allocation_bound_precedes_expansion():
    with pytest.raises(ValueError, match="dimension bound"):
        common_sample_grid(1009, 1013, 1019, 1021, 3)
    with pytest.raises(ValueError, match="dimension bound"):
        common_sample_grid(641, 1, 653, 1, 3)
    with pytest.raises(ValueError, match="exceeding"):
        common_sample_grid(100, 101, 103, 107, 3, max_dimension=20000)
    with pytest.raises(ValueError, match="exceeding"):
        common_sample_grid(2, 1, 3, 1, 3, max_bytes=95)
    assert common_sample_grid(2, 1, 3, 1, 3, max_bytes=96) == (6, 1)


def test_replication_does_not_crop_last_row_or_column():
    assert repeat_sample_grid(bytes([1, 2, 3, 4]), 2, 2, 1, 4, 4) == bytes([
        1, 1, 2, 2, 1, 1, 2, 2, 3, 3, 4, 4, 3, 3, 4, 4,
    ])
    with pytest.raises(ValueError, match="integer multiple"):
        repeat_sample_grid(bytes([1, 2]), 2, 1, 1, 3, 1)
    with pytest.raises(ValueError, match="sample bytes"):
        repeat_sample_grid(bytes([1]), 2, 1, 1, 4, 1)


@pytest.mark.parametrize("rotation", [0, 90, 270])
def test_aligned_samples_keep_original_sheared_quad(rotation, tmp_path):
    doc, xref, _mask_xref = image_document()
    with doc:
        page = doc[0]
        content = page.get_contents()[0]
        doc.update_stream(content, b"q 60 12 9 30 10 20 cm /fzImg0 Do Q")
        page.set_rotation(rotation)
        matrix = page.get_image_rects(xref, transform=True)[0][1]
        expected = []
        for x, y in ((0, 1), (1, 1), (1, 0), (0, 0)):
            point = fitz.Point(x, y) * matrix * page.rotation_matrix
            expected.append((point.x * 25.4 / 72, (page.rect.height - point.y) * 25.4 / 72))
        placements = bl_import_engine._extract_image_placements(
            doc, page, 1, types.SimpleNamespace(flip_y=True, user_scale=1.0), str(tmp_path),
        )
        assert len(placements) == 1
        assert placements[0]["soft_mask_alignment"] == {
            "method": "exact_common_sample_grid", "source_size": [2, 1], "mask_size": [3, 1],
            "output_size": [6, 1], "source_samples_preserved": True, "interpolate": False,
        }
        for actual, wanted in zip(placements[0]["quad_mm"], expected, strict=True):
            assert actual == pytest.approx(wanted)
        pix = fitz.Pixmap(str(Path(placements[0]["path"])))
        assert (pix.width, pix.height, pix.alpha) == (6, 1, 1)
