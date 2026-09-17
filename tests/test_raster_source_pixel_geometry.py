"""A source PDF crop must occupy its actual rendered pixel rectangle."""
import types

import pymupdf
import pytest

from pdf_vector_importer import bl_import_engine
from pdf_vector_importer.pdfcadcore.primitive_extractor import extract_page
from pdf_vector_importer.raster_geometry import source_bbox_to_model


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
        requested = pymupdf.Rect(item.source_bbox_pdf) * page.rotation_matrix
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
