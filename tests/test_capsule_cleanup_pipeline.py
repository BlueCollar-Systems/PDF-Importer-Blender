"""Visible marks survive the real default post-extraction cleanup pass."""
from copy import deepcopy
from types import SimpleNamespace

import pymupdf
import pytest

from pdf_vector_importer.bl_source_capsules import (
    prepare_capsules, cleanup_preserving_capsules,
)
from pdf_vector_importer.pdfcadcore.geometry_cleanup import cleanup_primitives
from pdf_vector_importer.pdfcadcore.import_config import ImportConfig
from pdf_vector_importer.pdfcadcore.primitive_extractor import extract_page


@pytest.mark.parametrize("length", [0.01, 0.02])
def test_reopened_source_capsule_survives_actual_default_cleanup(length):
    with pymupdf.open() as document:
        page = document.new_page(width=100, height=100)
        page.draw_line((10, 10), (10.01, 10), color=(0, 0, 0), width=0.1)
        resource = int(document.xref_get_key(page.xref, "Resources")[1].split()[0])
        document.xref_set_key(resource, "ExtGState", "<< /M << /BM /Multiply /CA 1 /ca 1 >> >>")
        raw = page.read_contents() + f"\nq /M gs 1 .5 0 RG 12 w 1 J 60 60 m {60+length} 60 l S Q".encode()
        xref = document.get_new_xref()
        document.update_object(xref, "<<>>")
        document.update_stream(xref, raw)
        page.set_contents(xref)
        encoded = document.tobytes()
    with pymupdf.open(stream=encoded, filetype="pdf") as source:
        page = source[0]
        data = extract_page(page, 1, detect_arcs=False)
        specs, unresolved = prepare_capsules(page, data, source_sha256="a"*64, page_number=1)
        assert len(specs) == 1 and not unresolved
        config = ImportConfig()
        baseline = deepcopy(data.primitives)
        cleanup_primitives(baseline, cleanup_level=config.cleanup_level)
        assert not baseline, "The old default cleanup must reproduce removal of both short lines"
        mark = next(p for p in data.primitives if p.id == specs[0]["primitive_id"])
        before = deepcopy(vars(mark))
        stats = cleanup_preserving_capsules(data.primitives, specs, cleanup_primitives, cleanup_level=config.cleanup_level)
        assert data.primitives == [mark]
        assert vars(mark) == before
        assert stats["removed_micro"] == 1 and stats["preserved_source_capsules"] == 1
        rebound, pending = prepare_capsules(page, data, source_sha256="a"*64, page_number=1)
        assert rebound == specs and not pending


@pytest.mark.parametrize("change", ["moved", "missing", "duplicate"])
def test_changed_qualified_centerline_fails_before_cleanup(change):
    point = SimpleNamespace(id=9, points=[(1., 2.), (1.001, 2.)])
    spec = {"primitive_id":9, "points_mm":deepcopy(point.points)}
    values = [point]
    if change == "moved":point.points[0]=(4., 5.)
    elif change == "missing":values=[]
    else:values.append(point)
    called=[]
    with pytest.raises(ValueError, match="changed before cleanup"):
        cleanup_preserving_capsules(values, [spec], lambda *a, **k:called.append(True))
    assert not called
