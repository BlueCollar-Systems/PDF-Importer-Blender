"""Source PDF descriptor heights recover anisotropic glyph Y, including reuse."""
from dataclasses import replace
import math
import pytest
import pymupdf as fitz
from test_representation_fidelity_blender import _item, _Object, _FontData
from pdf_vector_importer import bl_text_builder as builder
from pdf_vector_importer.pdfcadcore.primitive_extractor import extract_page

FACTOR = 25.4 / 72.0 * .001


def fraction_pdf(path, shear, rotated):
    doc=fitz.open();page=doc.new_page(width=200,height=150)
    page.insert_text((30,100),"13/16",fontname="helv",fontsize=1)
    parts=["BT /helv 1 Tf"]
    for text,x,y in [("13",30,50),("/",43.344,46),("16",46.68,42)]:
        matrix=(0,12,-20,shear,180-y,x) if rotated else (12,0,shear,20,x,y)
        parts.append(" ".join(map(str,matrix))+" Tm ("+text+") Tj")
    parts.append("ET");doc.update_stream(page.get_contents()[0]," ".join(parts).encode())
    doc.save(path);doc.close()


def extracted(path):
    with fitz.open(path) as doc:
        return next(t for t in extract_page(doc[0],1).text_items if t.text=="13/16")


@pytest.mark.parametrize("shear",[0.,4.,-3.])
@pytest.mark.parametrize("rotated",[False,True])
@pytest.mark.parametrize("template_size",[.003,.012])
def test_true_y_axes_and_baselines_survive_template_reuse(tmp_path,shear,rotated,template_size):
    source=tmp_path/"affine.pdf";fraction_pdf(source,shear,rotated)
    item=extracted(source)
    for layout in item.source_char_layout:
        parent=_item();parent.font_size=math.sqrt(240)*25.4/72
        # Fixture font assets exercise Blender normalization independently of
        # the source descriptor. Glyph identity is immaterial to this Y axis.
        layout=replace(layout,glyph_id=37)
        child=builder._character_text_item(parent,layout)
        data=_FontData("Template");data.size=template_size
        obj=_Object("Template",data);obj["pdf_converted_template_reused"]=True
        metrics=builder._positioned_font_axis_metrics(obj,child)
        matrix=builder._metric_character_matrix_values(
            local_advance=metrics["local_advance"],local_line_height=metrics["local_line_height"],
            target_origin=child.insertion,target_quad=child.target_quad_model,z=.00035)
        local_em=metrics["rendered_unit_m"]*metrics["units_per_em"]
        actual=(matrix[0][1]*local_em,matrix[1][1]*local_em)
        expected=(-20*FACTOR,shear*FACTOR) if rotated else (shear*FACTOR,20*FACTOR)
        assert actual==pytest.approx(expected,abs=2e-9)
        actual_x=(matrix[0][0]*local_em,matrix[1][0]*local_em)
        expected_x=(0.,12*FACTOR) if rotated else (12*FACTOR,0.)
        assert actual_x==pytest.approx(expected_x,abs=2e-9)
        assert (matrix[0][3],matrix[1][3])==pytest.approx(tuple(v*.001 for v in layout.target_origin))
        assert child.source_char_layout==(layout,)
        assert not child.requires_individual_positioning


@pytest.mark.parametrize("mutation",[
    {"source_font_ascender":None},{"source_font_descender":float("nan")},
    {"source_font_size_pdf":0.},{"source_writing_mode":1},{"source_writing_mode":False},
    {"source_font_ascender":2.},{"source_origin_pdf":(0.,0.)},
])
def test_unavailable_or_unbound_descriptor_fails_without_approximation(tmp_path,mutation):
    source=tmp_path/"affine.pdf";fraction_pdf(source,4.,False)
    layout=extracted(source).source_char_layout[0]
    with pytest.raises(RuntimeError,match="unavailable or unbound"):
        builder._source_character_font_height(replace(layout,**mutation))


def test_unavailable_optional_metric_api_preserves_actual_source_quads(tmp_path, monkeypatch):
    from pdf_vector_importer.pdfcadcore.primitive_extractor import _raw_text_with_source_quads

    source = tmp_path / "metrics-unavailable.pdf"
    fraction_pdf(source, shear=4.0, rotated=False)
    with fitz.open(source) as doc:
        before = _raw_text_with_source_quads(doc[0])
        def unavailable(_font):
            raise AttributeError("older wrapper has no original-font metric getter")
        monkeypatch.setattr(fitz.mupdf, "ll_fz_font_ascender", unavailable)
        after = _raw_text_with_source_quads(doc[0])
    def chars(raw):
        return [c for b in raw["blocks"] if b["type"] == 0
                for line in b["lines"] for span in line["spans"] for c in span["chars"]]
    original, retained = chars(before), chars(after)
    assert len(original) == len(retained) == 5
    for a, b in zip(original, retained, strict=True):
        assert b["c"] == a["c"] and b["origin"] == a["origin"]
        assert b["quad"] == a["quad"]
        assert all(key not in b for key in (
            "source_font_size_pdf", "source_font_ascender",
            "source_font_descender", "source_writing_mode",
        ))



def test_declared_advance_changes_do_not_stretch_exact_glyph_ink(tmp_path):
    source=tmp_path/"affine.pdf";fraction_pdf(source,4.,False)
    char=extracted(source).source_char_layout[0]
    def expanded(quad):
        ul,ur,lr,ll=quad
        return (ul,tuple(ul[i]+1.8*(ur[i]-ul[i]) for i in range(2)),
                tuple(ll[i]+1.8*(lr[i]-ll[i]) for i in range(2)),ll)
    other=replace(char,source_quad_pdf=expanded(char.source_quad_pdf),
                  target_quad=expanded(char.target_quad),advance_width=char.advance_width*1.8)
    matrices=[]
    for layout in [char,other]:
        child=builder._character_text_item(_item(),replace(layout,glyph_id=37))
        data=_FontData("Outline");data.size=.012;obj=_Object("Outline",data)
        metrics=builder._positioned_font_axis_metrics(obj,child)
        matrices.append(builder._metric_character_matrix_values(
            local_advance=metrics["local_advance"],local_line_height=metrics["local_line_height"],
            target_origin=child.insertion,target_quad=child.target_quad_model,z=0))
    for before,after in zip(matrices[0],matrices[1],strict=True):
        assert after==pytest.approx(before,abs=1e-12)
