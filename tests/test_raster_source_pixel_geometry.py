"""A source PDF crop must occupy its actual rendered pixel rectangle."""
import types
import sys
from dataclasses import replace

import pymupdf
import pytest

if "bpy" not in sys.modules:
    sys.modules["bpy"] = types.SimpleNamespace()
if not hasattr(sys.modules["bpy"], "app"):
    sys.modules["bpy"].app = types.SimpleNamespace(version=(4, 1, 0))
if not hasattr(sys.modules["bpy"], "types"):
    sys.modules["bpy"].types = types.SimpleNamespace(Collection=object, Object=object)
if "bmesh" not in sys.modules:
    sys.modules["bmesh"] = types.SimpleNamespace()

from pdf_vector_importer import bl_import_engine
from pdf_vector_importer.pdfcadcore.primitive_extractor import extract_page
from pdf_vector_importer.raster_geometry import source_bbox_to_model, source_raster_bounds


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_original_pixel_grid_controls_model_crop_not_font_box(tmp_path, monkeypatch, rotation):
    with pymupdf.open() as doc:
        page = doc.new_page(width=300, height=240)
        page.insert_text((30.13, 95.17), "F A", fontsize=13.3,
                         morph=(pymupdf.Point(30.13, 95.17), pymupdf.Matrix(1.3, 1)))
        page.draw_rect(pymupdf.Rect(35, 80, 70, 105), fill=(0,1,1), fill_opacity=.3)
        page.set_rotation(rotation)
        item = extract_page(page, 1, detect_arcs=False).text_items[0]
        captured = []
        obj = {}
        monkeypatch.setattr(bl_import_engine, "_create_image_plane",
                            lambda placement, *a, **k: captured.append(placement) or obj)
        result = bl_import_engine._render_text_item_raster(
            page, item, object(), page_num=1, item_id="page:1:text:1",
            import_cfg=types.SimpleNamespace(raster_dpi=300), image_dir=str(tmp_path))
        assert result is obj
        points=[p for char in item.source_char_layout for p in char.source_quad_pdf]
        b=item.source_bbox_pdf
        expected_coverage=(min(b[0],*(p[0] for p in points)), min(b[1],*(p[1] for p in points)),
                           max(b[2],*(p[0] for p in points)), max(b[3],*(p[1] for p in points)))
        requested = pymupdf.Rect(expected_coverage) * page.rotation_matrix
        pix = page.get_pixmap(matrix=pymupdf.Matrix(300/72,300/72),clip=requested,alpha=False)
        source_bounds = pymupdf.Rect(pix.x*72/300,pix.y*72/300,
                                    (pix.x+pix.width)*72/300,(pix.y+pix.height)*72/300)
        original_bounds = source_bounds * page.derotation_matrix
        assert captured[0]["source_pixel_bbox_pdf"] == pytest.approx(tuple(original_bounds))
        scale = 25.4/72
        expected = (source_bounds.x0*scale, (page.rect.height-source_bounds.y1)*scale,
                    source_bounds.x1*scale, (page.rect.height-source_bounds.y0)*scale)
        actual = source_bbox_to_model(item, original_bounds)
        assert actual == pytest.approx(expected, abs=1e-5)
        assert captured[0]["x_mm"] == pytest.approx(expected[0], abs=1e-5)
        assert captured[0]["y_mm"] == pytest.approx(expected[1], abs=1e-5)
        # The packed crop is the renderer's exact opaque final page composite.
        saved = pymupdf.Pixmap(captured[0]["path"])
        assert saved.alpha == 0
        assert saved.samples == pix.samples
        assert obj["pdf_raster_final_page_composite"] is True


def test_short_font_bbox_does_not_trim_real_glyph_ink(tmp_path, monkeypatch):
    with pymupdf.open() as doc:
        page=doc.new_page(width=300,height=240)
        page.insert_text((100,150),"H",fontsize=12)
        original=extract_page(page,1,detect_arcs=False).text_items[0]
        # Model a PDF font bbox shorter than its original glyph quad. There is
        # real source ink above y145, so rendering only this bbox would trim H.
        b=original.source_bbox_pdf
        item=replace(original,source_bbox_pdf=(b[0],145,b[2],b[3]))
        captured=[];obj={}
        monkeypatch.setattr(bl_import_engine,"_create_image_plane",
                            lambda placement,*a,**k:captured.append(placement) or obj)
        assert bl_import_engine._render_text_item_raster(page,item,object(),page_num=1,
                item_id="glyph-coverage",import_cfg=types.SimpleNamespace(raster_dpi=300),image_dir=str(tmp_path)) is obj
        assert tuple(obj['pdf_raster_source_bbox_pdf'])==item.source_bbox_pdf
        quad=original.source_char_layout[0].source_quad_pdf
        coverage=(min(p[0] for p in quad),min(p[1] for p in quad),max(p[0] for p in quad),max(p[1] for p in quad))
        assert obj['pdf_raster_coverage_bbox_pdf']==pytest.approx(coverage)
        expected=page.get_pixmap(matrix=pymupdf.Matrix(300/72,300/72),clip=pymupdf.Rect(coverage),alpha=False)
        saved=pymupdf.Pixmap(captured[0]['path'])
        assert saved.samples==expected.samples
        rows_above_crop=int(145*300/72)-expected.y
        assert any(value<128 for value in expected.samples[:rows_above_crop*expected.stride])


def test_source_coverage_rejects_nonfinite_original_quad():
    item=types.SimpleNamespace(source_bbox_pdf=(0,0,10,10),source_char_layout=(),
                              source_quad_pdf=((0,0),(10,0),(10,float('nan')),(0,10)))
    with pytest.raises(ValueError,match='not finite'):
        source_raster_bounds(item)
