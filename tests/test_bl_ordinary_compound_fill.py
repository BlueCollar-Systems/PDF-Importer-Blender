"""A filled PDF counter is empty ink, never a second independently filled face."""
from copy import deepcopy

import pymupdf
import pytest

from pdf_vector_importer.bl_compound_fills import (
    CompoundTopologyBudgetExceeded, bind_nonzero_windings, grouped_fill_primitives, nonzero_fill_contours,
    split_self_crossing_contours,
)
from pdf_vector_importer.pdfcadcore.primitive_extractor import extract_page


def source_page(*, operator="f*", extra=b""):
    doc = pymupdf.open()
    page = doc.new_page(width=100, height=100)
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, b"0 .5 0 rg 10 10 80 70 re 30 30 30 20 re "
                      + operator.encode() + b"\n" + extra)
    page.set_contents(xref)
    return doc, page


def test_real_source_evenodd_counter_groups_without_editing_vertices_or_source():
    with source_page()[0] as doc:
        page = doc[0]
        pix = page.get_pixmap()
        assert pix.pixel(40, 60) == (255, 255, 255)  # PDF's counter is unpainted.
        assert pix.pixel(20, 60)[1] < 255  # The surrounding ring contains green ink.
        data = extract_page(page, 1, detect_arcs=False)
        original = deepcopy(data.primitives)
        grouped = grouped_fill_primitives(data)
    assert data.primitives == original
    assert len(grouped) == 2 and len({p.clip_fill_group_id for p in grouped}) == 1
    assert all(p.closed and p.clip_fill_even_odd for p in grouped)
    assert [p.points for p in grouped] == [p.points for p in original]
    assert sum(p.area for p in original[:1]) - original[1].area == pytest.approx(
        (80 * 70 - 30 * 20) * (25.4 / 72) ** 2)


def test_separate_fill_occurrences_are_never_combined_by_matching_colour():
    with source_page(extra=b"0 .5 0 rg 15 15 5 5 re f*")[0] as doc:
        data = extract_page(doc[0], 1, detect_arcs=False)
    grouped = grouped_fill_primitives(data)
    assert len(grouped) == 3
    assert grouped[-1].clip_fill_group_id is None


def test_compound_nonzero_fill_is_kept_together_but_not_guessed_as_evenodd():
    with source_page(operator="f")[0] as doc:
        data = extract_page(doc[0], 1, detect_arcs=False)
    grouped = grouped_fill_primitives(data)
    assert len(grouped) == 2 and all(p.clip_fill_group_id for p in grouped)
    assert all(p.clip_fill_even_odd is False for p in grouped)


def test_lone_source_evenodd_bowtie_is_not_discarded_as_zero_signed_area():
    with pymupdf.open() as doc:
        page = doc.new_page(width=100, height=100)
        xref = doc.get_new_xref()
        doc.update_object(xref, "<<>>")
        doc.update_stream(xref, b"0 .5 0 rg 10 10 m 20 20 l 10 20 l 20 10 l h f*")
        page.set_contents(xref)
        data = extract_page(page, 1, detect_arcs=False)
        assert any(page.get_pixmap().pixel(x, 82)[1] < 255 for x in range(11, 19))
    original = deepcopy(data.primitives)
    cache = {}
    grouped = grouped_fill_primitives(data, topology_cache=cache)
    assert len(grouped) == 1 and grouped[0].clip_fill_group_id == "pdf-fill:0"
    assert len(cache["pdf-fill:0"]) == 2
    assert data.primitives == original


@pytest.mark.parametrize("limits,reason", [
    ({"point_budget": 3}, "points"), ({"work_budget": 3}, "work"),
    ({"candidate_budget": 1}, "candidates"), ({"split_budget": 1}, "splits"),
])
def test_topology_budget_is_bounded_and_never_changes_original_source_paths(limits, reason):
    source = [[(0, 0), (10, 10), (0, 10), (10, 0)],
              [(20, 0), (30, 10), (20, 10), (30, 0)]]
    original = deepcopy(source)
    with pytest.raises(CompoundTopologyBudgetExceeded, match=reason + " budget exceeded"):
        split_self_crossing_contours(source, **limits)
    assert source == original


def test_source_topology_cancel_is_checked_before_native_allocation():
    from pdf_vector_importer.import_session import ImportCancelledError

    source = [[(0, 0), (10, 10), (0, 10), (10, 0)]]
    with pytest.raises(ImportCancelledError, match="compound fill topology"):
        split_self_crossing_contours(source, progress_callback=lambda: False)


def test_lone_over_budget_fill_enters_the_reported_preserved_path_route():
    with source_page(extra=b"0 .5 0 rg 15 15 5 5 re f*")[0] as doc:
        data = extract_page(doc[0], 1, detect_arcs=False)
    grouped = grouped_fill_primitives(data, limits={"point_budget": 2})
    assert grouped[-1].clip_fill_group_id == "pdf-fill:1"


def test_actual_source_opposite_winding_counter_keeps_empty_ink():
    with pymupdf.open() as doc:
        page = doc.new_page(width=100, height=100)
        xref = doc.get_new_xref()
        doc.update_object(xref, "<<>>")
        doc.update_stream(xref, b"0 .5 0 rg 10 10 m 90 10 l 90 80 l 10 80 l h "
                          b"30 30 m 30 50 l 60 50 l 60 30 l h f")
        page.set_contents(xref)
        assert page.get_pixmap().pixel(40, 60) == (255, 255, 255)
        assert page.get_pixmap().pixel(20, 60)[1] < 255
        data = extract_page(page, 1, detect_arcs=False)
        winding_proof = bind_nonzero_windings(data, page.get_svg_image(text_as_path=False))
    original = deepcopy(data.primitives)
    cache = {}
    grouped = grouped_fill_primitives(data, topology_cache=cache, nonzero_windings=winding_proof)
    assert len(grouped) == 2 and all(p.clip_fill_even_odd is False for p in grouped)
    loops = nonzero_fill_contours([p.points for p in grouped], source_windings=cache["pdf-fill:0:winding"])
    assert len(loops) == 2 and data.primitives == original
    area = lambda ring: abs(sum(a[0]*b[1]-b[0]*a[1]
                               for a, b in zip(ring, ring[1:]+ring[:1], strict=True)))/2
    assert area(loops[0])-area(loops[1]) == pytest.approx((80*70-30*20)*(25.4/72)**2)


def test_nonzero_same_winding_inner_boundary_is_removed_but_nested_island_is_kept():
    outer = [(0, 0), (10, 0), (10, 10), (0, 10)]
    inner = [(3, 3), (7, 3), (7, 7), (3, 7)]
    island = [(4, 4), (6, 4), (6, 6), (4, 6)]
    assert nonzero_fill_contours([outer, inner]) == [outer]
    assert len(nonzero_fill_contours([outer, inner[::-1], island])) == 3
    assert len(nonzero_fill_contours([outer[::-1], inner, island[::-1]])) == 3


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("start", range(4))
def test_original_svg_winding_survives_all_rectangle_start_and_direction_rotations(reverse, start):
    points = [(30,30),(30,50),(60,50),(60,30)]
    if reverse:
        points.reverse()
    points = points[start:]+points[:start]
    commands = f"0 .5 0 rg 10 10 80 70 re {points[0][0]} {points[0][1]} m "
    commands += " ".join(f"{x} {y} l" for x,y in points[1:])+" h f"
    with pymupdf.open() as doc:
        page = doc.new_page(width=100,height=100)
        xref=doc.get_new_xref();doc.update_object(xref,"<<>>")
        doc.update_stream(xref,commands.encode());page.set_contents(xref)
        hole_white=page.get_pixmap().pixel(40,60)==(255,255,255)
        data=extract_page(page,1,detect_arcs=False)
        proof=bind_nonzero_windings(data,page.get_svg_image(text_as_path=False))
    assert bool(proof)
    cache={}
    grouped=grouped_fill_primitives(data,topology_cache=cache,nonzero_windings=proof)
    selected=nonzero_fill_contours([p.points for p in grouped],source_windings=cache['pdf-fill:0:winding'])
    assert len(selected)==(2 if hole_white else 1)


def test_unavailable_or_ambiguous_source_svg_never_supplies_guessed_nonzero_winding():
    with source_page(operator="f")[0] as doc:
        data=extract_page(doc[0],1,detect_arcs=False)
        svg=doc[0].get_svg_image(text_as_path=False)
    assert bind_nonzero_windings(data,None)=={}
    assert bind_nonzero_windings(data,svg.replace('</svg>',svg[svg.index('<path'):svg.index('</svg>')]+'</svg>'))=={}
    assert bind_nonzero_windings(data,svg.replace('<path','<path clip-path="url(#unknown)"'))=={}
    assert bind_nonzero_windings(data,svg.replace('</svg>','<style>path{fill-rule:evenodd}</style></svg>'))=={}
    assert bind_nonzero_windings(data,svg,work_budget=1)=={}
    from pdf_vector_importer.import_session import ImportCancelledError

    with pytest.raises(ImportCancelledError):
        bind_nonzero_windings(data,svg,progress_callback=lambda:False)


def test_safe_directed_path_cannot_certify_a_second_equal_shape_under_unsafe_blend():
    with pymupdf.open() as doc:
        page=doc.new_page(width=100,height=100)
        blend=doc.get_new_xref();doc.update_object(blend,"<< /Type /ExtGState /BM /Multiply >>")
        doc.xref_set_key(page.xref,"Resources",f"<< /ExtGState << /M {blend} 0 R >> >>")
        xref=doc.get_new_xref();doc.update_object(xref,"<<>>")
        doc.update_stream(xref,b"0 .5 0 rg 10 10 80 70 re 30 30 m 30 50 l 60 50 l 60 30 l h f "
                          b"q /M gs 10 10 80 70 re 30 30 m 60 30 l 60 50 l 30 50 l h f Q")
        page.set_contents(xref)
        assert page.get_pixmap().pixel(40,60)[1]<255
        data=extract_page(page,1,detect_arcs=False)
        svg=page.get_svg_image(text_as_path=False)
        assert 'mix-blend-mode:multiply' in svg
        assert bind_nonzero_windings(data,svg)=={}


@pytest.mark.parametrize("contours", [
    [[(0, 0), (10, 0), (10, 10), (0, 10)], [(5, 0), (15, 0), (15, 10), (5, 10)]],
    [[(0, 0), (10, 0), (10, 10), (0, 10)], [(10, 0), (20, 0), (20, 10), (10, 10)]],
    [[(0, 0), (10, 10), (0, 10), (10, 0)]],
])
def test_ambiguous_nonzero_crossings_are_not_guessed_as_parity(contours):
    original = deepcopy(contours)
    with pytest.raises(ValueError, match="winding-aware arrangement"):
        nonzero_fill_contours(contours)
    assert contours == original


@pytest.mark.parametrize("change", ["missing", "duplicate", "colour", "opacity", "order"])
def test_incomplete_or_ambiguous_source_membership_fails_before_native_allocation(change):
    with source_page()[0] as doc:
        data = extract_page(doc[0], 1, detect_arcs=False)
    if change == "missing":
        data.primitives.pop()
    elif change == "duplicate":
        data.primitives[1] = data.primitives[0]
    elif change == "colour":
        data.primitives[1].fill_color = (1, 0, 0)
    elif change == "opacity":
        data.primitives[1].fill_opacity = .5
    else:
        data._source_drawings.append(dict(data._source_drawings[0]))
    with pytest.raises(ValueError, match="compound fill"):
        grouped_fill_primitives(data)


def test_proper_self_crossing_is_split_into_exact_edge_equivalent_simple_loops():
    source=[[(0,0),(10,10),(0,10),(10,0),(0,0)],[(20,0),(25,0),(25,5),(20,5)]]
    before=deepcopy(source)
    split=split_self_crossing_contours(source)
    assert source==before
    assert len(split)==3
    area=lambda ring:abs(sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(ring,ring[1:]+ring[:1],strict=True)))/2
    assert sum(area(ring) for ring in split)==75
    assert all((5.,5.) in ring for ring in split if (20.,0.) not in ring)


def test_repeated_interior_vertex_keeps_both_touching_cycles():
    source=[[(0,0),(5,0),(5,5),(0,0),(-5,0),(-5,-5)]]
    split=split_self_crossing_contours(source)
    assert len(split)==2 and all(len(ring)==3 for ring in split)


def test_touching_contour_order_is_spatially_stable_with_source_data_unchanged():
    left=[(0,0),(10,0),(10,10),(0,10)]
    right=[(10,0),(20,0),(20,10),(10,10)]
    source=[right,left]
    before=deepcopy(source)
    assert split_self_crossing_contours(source)==[left,right]
    assert source==before
