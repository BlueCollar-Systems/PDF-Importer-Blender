"""Cross-band ordering requires original paint absence and native ownership."""
from types import SimpleNamespace as NS

import pymupdf as fitz
import pytest

from pdf_vector_importer.triangle_paint_order import (
    _triangle_path, _svg_triangles, plan_terminal_triangles, apply_terminal_triangles,
    _source_points_match,
)
from pdf_vector_importer.pdfcadcore.primitive_extractor import extract_page
from pdf_vector_importer.opaque_rectangle_order import _same_cycle


@pytest.mark.parametrize('path', ['M0 0L10 0L5 10Z', 'M0 0 10 0 5 10', 'M0 0H10L5 10L0 0Z'])
def test_one_absolute_fill_triangle_preserves_original_vertices(path):
    assert _triangle_path(path) == [(0., 0.), (10., 0.), (5., 10.)]


def test_source_contour_cycle_accepts_exact_duplicate_closure_on_both_sides():
    assert _same_cycle([(0., 0.), (10., 0.), (5., 10.), (0., 0.)],
                       [(5., 10.), (10., 0.), (0., 0.), (5., 10.)])


@pytest.mark.parametrize('path', ['M0 0l10 0 5 10Z', 'M0 0L10 0Q5 5 5 10Z',
                                 'M0 0L10 0L20 0Z', 'M0 0L10 0L5 10Z M0 0',
                                 'M0 0L10 0L5 10L0 10Z', 'M0 0L10 0L5 10L'])
def test_unknown_commands_compound_paths_and_degenerate_triangles_reject(path):
    assert _triangle_path(path) is None


def test_svg_clip_and_group_opacity_cannot_be_ignored():
    body = '<path d="M0 0L10 0L5 10Z" fill="#000000"/>'
    assert len(_svg_triangles('<svg>'+body+'</svg>')) == 1
    assert _svg_triangles('<svg><g opacity=".5">'+body+'</g></svg>') == []
    assert _svg_triangles('<svg><g mask="url(#m)">'+body+'</g></svg>') == []
    clipped = '<svg><defs><clipPath id="c"><path d="M0 0H4V4H0Z"/></clipPath></defs><g clip-path="url(#c)">'+body+'</g></svg>'
    assert _svg_triangles(clipped) == []


@pytest.mark.parametrize('wrapper', [
    '<svg><svg x="20" viewBox="0 0 1 1">{}</svg></svg>',
    '<svg><style>path {{fill-opacity:.2}}</style>{}</svg>',
    '<svg><defs><clipPath id="c"><path d="M0 0H1V1H0Z"/></clipPath>'
    '<clipPath id="c"><path d="M0 0H20V20H0Z"/></clipPath></defs>'
    '<g clip-path="url(#c)">{}</g></svg>',
    '<?xml-stylesheet type="text/css" href="paint.css"?><svg>{}</svg>',
])
def test_ambiguous_clip_css_or_nested_viewport_cannot_certify_source_fill(wrapper):
    assert _svg_triangles(wrapper.format('<path d="M0 0L10 0L5 10Z"/>')) == []


def source_case(tmp_path, later=None, mutate=None, outline=False, planner=plan_terminal_triangles):
    path = tmp_path / 'source.pdf'
    with fitz.open() as doc:
        page = doc.new_page(width=100, height=100)
        page.draw_rect((10, 10, 80, 80), color=None, fill=(1, 1, 1))
        page.draw_polyline([(25, 25), (35, 25), (30, 35)], closePath=True,
                           color=(0, 0, 0) if outline else None, fill=(0, 0, 0),
                           width=2, lineCap=1, lineJoin=1)
        if later == 'fill':
            page.draw_rect((27, 27, 32, 32), color=None, fill=(1, 0, 0))
        elif later == 'text':
            page.insert_text((25, 32), 'A', fontsize=12)
        elif later == 'stroke':
            page.draw_line((20, 30), (40, 30), width=1)
        doc.save(path)
    with fitz.open(path) as doc:
        page = doc[0]
        data = extract_page(page, 1, detect_arcs=False)
        white = next(p for p in data.primitives if p.source_draw_order == 0)
        # The source rectangle represents one retained clip-contour group in
        # this consumer fixture; its source event and points remain original.
        white.clip_fill_group_id = 'opaque-owned-group'
        white.clip_fill_even_odd = True
        if mutate:
            mutate(data, white)
        return planner(page, data, 'a'*64)


def test_saved_pdf_original_event_and_normalized_contours_bind_without_name_guess(tmp_path):
    plans, unresolved = source_case(tmp_path)
    assert unresolved == [] and len(plans) == 1
    assert plans[0]['source_draw_order'] == 1
    assert plans[0]['earlier_compound_contours'][0]['source_draw_order'] == 0
    assert plans[0]['later_source_paint_absent'] is True


def test_combined_fill_stroke_is_one_owned_paint_pair_with_full_round_outline(tmp_path):
    plans, unresolved = source_case(tmp_path, outline=True)
    assert unresolved == [] and len(plans) == 1
    plan = plans[0]
    assert plan['fill_event'] + 1 == plan['stroke_event']
    assert plan['outline']['width_pdf'] == 2
    assert plan['dependency_bounds_mm'][0] == pytest.approx(24*25.4/72)
    assert plan['dependency_bounds_mm'][2] == pytest.approx(36*25.4/72)


@pytest.mark.parametrize('later', ['fill', 'text', 'stroke'])
def test_combined_paint_does_not_skip_unrelated_later_paints(tmp_path, later):
    plans, unresolved = source_case(tmp_path, outline=True, later=later)
    assert plans == [] and unresolved[0]['reason'] == 'later_source_paint_overlaps_triangle'


def test_earlier_identical_unclipped_stroke_cannot_certify_later_clipped_fs(tmp_path):
    path = tmp_path / 'own-outline-clipped.pdf'
    points = [(25, 25), (35, 25), (30, 35)]
    with fitz.open() as doc:
        page = doc.new_page(width=100, height=100)
        page.draw_rect((10, 10, 80, 80), color=None, fill=(1, 1, 1))
        page.draw_polyline(points, closePath=True, color=(0, 0, 0), fill=None,
                           width=2, lineCap=1, lineJoin=1)
        page.draw_polyline(points, closePath=True, color=(0, 0, 0), fill=(0, 0, 0),
                           width=2, lineCap=1, lineJoin=1)
        xref = page.get_contents()[-1]
        doc.update_stream(xref, b'q 25 65 10 10 re W n\n'+doc.xref_stream(xref)+b'\nQ')
        doc.save(path)
    with fitz.open(path) as doc:
        page = doc[0]
        data = extract_page(page, 1, detect_arcs=False)
        white = next(p for p in data.primitives if p.source_draw_order == 0)
        white.clip_fill_group_id = 'original-white'
        rows = _svg_triangles(page.get_svg_image(text_as_path=False))
        assert len([r for r in rows if r['kind'] == 'stroke']) == 1
        assert len([r for r in rows if r['kind'] == 'fill']) == 1
        plans, unresolved = plan_terminal_triangles(page, data, 'a'*64)
        assert plans == []
        assert any(r['reason'] == 'triangle_source_outline_not_uniquely_bound' for r in unresolved)


def test_rejected_live_svg_paint_still_breaks_fill_outline_adjacency():
    svg = ('<svg><path d="M0 0L10 0L5 10Z"/>'
           '<path d="M0 0Q5 5 10 0" stroke="#000000" fill="none"/>'
           '<path d="M0 0L10 0L5 10Z" stroke="#000000" fill="none" '
           'stroke-width="1" stroke-linecap="round" stroke-linejoin="round"/></svg>')
    rows = _svg_triangles(svg)
    assert [r['paint_ordinal'] for r in rows] == [0, 2]


def test_svg_operand_uncertainty_survives_translation_cancellation_but_not_real_movement():
    import struct
    f32 = lambda value: struct.unpack('f', struct.pack('f', value))[0]
    svg = '<svg><path transform="matrix(.12,0,0,-.12,0,1728)" d="M2000 14000L2050 14000L2025 14050Z"/></svg>'
    row = _svg_triangles(svg)[0]
    raw = [(f32(f32(.12)*x), f32(f32(f32(-.12)*y)+f32(1728)))
           for x, y in ((2000, 14000), (2050, 14000), (2025, 14050))]
    assert _source_points_match(row, raw)
    assert not _source_points_match(row, [(x, y+.01) for x, y in raw])


def cloud_case(tmp_path, *, top=10., width=2., line_join=1, repeat=False, planner=plan_terminal_triangles):
    path = tmp_path/'source-cloud.pdf'
    with fitz.open() as doc:
        page = doc.new_page(width=100, height=100)
        page.draw_rect((10, 10, 90, 90), color=None, fill=(1, 1, 1))
        page.draw_polyline([(25, 25), (35, 25), (30, 35)], closePath=True,
                           color=None, fill=(0, 0, 0))
        for _ in range(2 if repeat else 1):
            shape = page.new_shape()
            # Four cubic border segments have a large whole-path box, while
            # their control hulls leave the central triangle untouched.
            shape.draw_bezier((10, top), (30, top), (70, top), (90, top))
            shape.draw_bezier((90, top), (95, 35), (95, 70), (90, 90))
            shape.draw_bezier((90, 90), (70, 95), (30, 95), (10, 90))
            shape.draw_bezier((10, 90), (5, 70), (5, 35), (10, top))
            shape.finish(color=(1, 0, 0), width=width, lineCap=0,
                         lineJoin=line_join, closePath=False)
            shape.commit()
        doc.save(path)
    with fitz.open(path) as doc:
        page = doc[0]
        data = extract_page(page, 1, detect_arcs=False)
        white = next(p for p in data.primitives if p.source_draw_order == 0)
        white.clip_fill_group_id = 'source-white'
        return planner(page, data, 'a'*64)


def test_later_cubic_stroke_hulls_prove_disjoint_without_treating_bbox_interior_as_paint(tmp_path):
    plans, unresolved = cloud_case(tmp_path)
    assert unresolved == [] and len(plans) == 1
    proof = plans[0]['later_stroke_bound_exclusions']
    assert len(proof) == 1 and proof[0]['segment_count'] == 4
    assert proof[0]['source_draw_order'] == 2 and proof[0]['width_pdf'] == 2


@pytest.mark.parametrize('change', [
    {'top': 30.},  # actual crossing
    {'top': 24.},  # the circular pen footprint is tangent to the triangle
    {'width': 40.},  # centerline is clear, stroke paint is not
    {'line_join': 0},  # unbounded miter geometry is unsupported
    {'repeat': True},  # identical occurrences cannot be borrowed
])
def test_cubic_disjointness_rejects_crossing_tangent_wide_unknown_and_ambiguous_paint(tmp_path, change):
    plans, unresolved = cloud_case(tmp_path, **change)
    assert plans == [] and unresolved[0]['reason'] == 'later_source_paint_overlaps_triangle'


def test_owned_later_round_line_is_retained_in_source_order(tmp_path, monkeypatch):
    import pdf_vector_importer.triangle_paint_order as module
    original = module._later_stroke_plan
    calls = {}

    def counted(order, *args):
        calls[order] = calls.get(order, 0)+1
        return original(order, *args)

    monkeypatch.setattr(module, '_later_stroke_plan', counted)
    # The ordinary stroke-negative uses square/butt source styling, which is
    # deliberately outside this bounded round-stroke representation proof.
    path = tmp_path / 'later-round-line.pdf'
    with fitz.open() as doc:
        page = doc.new_page(width=100, height=100)
        page.draw_rect((10, 10, 90, 80), color=None, fill=(1, 1, 1))
        page.draw_polyline([(25, 25), (35, 25), (30, 35)], closePath=True,
                           color=(0, 0, 0), fill=(0, 0, 0), width=2, lineCap=1, lineJoin=1)
        page.draw_polyline([(30, 30), (70, 30)], closePath=False,
                           color=(0, 0, 0), width=2, lineCap=1, lineJoin=1)
        doc.save(path)
    with fitz.open(path) as doc:
        page = doc[0]
        data = extract_page(page, 1, detect_arcs=False)
        next(p for p in data.primitives if p.source_draw_order == 0).clip_fill_group_id = 'retained-white'
        plans, unresolved = plan_terminal_triangles(page, data, 'a'*64)
        assert unresolved == [] and len(plans) == 1
        plan = plans[0]
        assert len(plan['later_strokes']) == 1
        assert plan['later_unowned_paint_absent'] is True and plan['later_source_paint_absent'] is False
        assert plan['later_strokes'][0]['source_draw_order'] > plan['stroke_event']
        assert plan['dependency_bounds_mm'][2] == pytest.approx(71*25.4/72)
        assert list(calls.values()) == [1]  # closure repeats, immutable source join does not
        # A later label at the far end is inside the expanded line dependency,
        # though it is outside the original triangle: qualification must stop.
        page.insert_text((65, 32), 'X', fontsize=10)
        plans, unresolved = plan_terminal_triangles(page, extract_page(page, 1, detect_arcs=False), 'a'*64)
        assert plans == []
        assert any(r['reason'] == 'later_source_paint_overlaps_triangle' for r in unresolved)
        assert list(calls.values()) == [2]  # the changed page gets a fresh per-call cache


@pytest.mark.parametrize('later', ['fill', 'text', 'stroke'])
def test_any_later_intersecting_source_paint_prevents_cross_band_lift(tmp_path, later):
    plans, unresolved = source_case(tmp_path, later=later)
    assert plans == []
    assert unresolved[0]['reason'] == 'later_source_paint_overlaps_triangle'


@pytest.mark.parametrize('field,value', [('source_draw_order', None), ('source_draw_order', 2),
                                       ('fill_opacity', .5), ('source_fill_color', (0., 0., 0.))])
def test_unknown_later_transparent_or_changed_compound_never_qualifies(tmp_path, field, value):
    plans, unresolved = source_case(tmp_path, mutate=lambda data, white: setattr(white, field, value))
    assert plans == []
    assert unresolved[0]['reason'] == 'overlapping_compound_source_order_or_opacity_unknown'


def native_case(monkeypatch):
    from test_fill_paint_order import native_faces
    from test_image_paint_order_native import Vec

    class Point(Vec):
        def to_3d(self):
            return Vec(self[:3])

    collection, fills = native_faces(monkeypatch)
    white, arrow = fills
    obj = white['object']
    obj.type = 'CURVE'
    obj.data = NS(dimensions='2D', fill_mode='BOTH', bevel_depth=0., extrude=0.,
                  materials=obj.data.materials, splines=[NS(type='POLY', use_cyclic_u=True,
                    bezier_points=[], points=[NS(co=Point((x*.001, y*.001, 0., 1.)))
                                             for x, y in white['points_mm']])])
    compound = {'object': obj, 'primitive_ids': ['white'], 'source_draw_order': 4,
                'contours_mm': [white['points_mm']], 'fill_opacity': 1., 'fill_rgb': (1., 1., 1.),
                'visual_style': 'source', 'even_odd': True}
    plan = {'page': 1, 'primitive_id': 'arrow', 'source_draw_order': 9,
            'later_source_paint_absent': True, 'later_unowned_paint_absent': True,
            'points_mm': arrow['points_mm'], 'fill_rgb': [0., 0., 0.],
            'earlier_compound_contours': [{'primitive_id': 'white', 'source_draw_order': 4,
                'points_mm': white['points_mm'], 'fill_rgb': [1., 1., 1.], 'even_odd': True}]}
    config = {'_source_fill_objects': [arrow], '_source_compound_fill_objects': [compound]}
    return collection, arrow, compound, plan, config


def test_actual_curve_and_mesh_bands_reorder_without_changing_either_geometry(monkeypatch):
    collection, arrow, compound, plan, config = native_case(monkeypatch)
    before_arrow = [tuple(v.co) for v in arrow['object'].data.vertices]
    before_white = [tuple(p.co) for p in compound['object'].data.splines[0].points]
    result = apply_terminal_triangles([plan], collection, config)
    assert result[0]['status'] == 'applied'
    assert arrow['object'].location.z == pytest.approx(.00045)
    assert compound['object'].location.z == 0.
    assert [tuple(v.co) for v in arrow['object'].data.vertices] == before_arrow
    assert [tuple(p.co) for p in compound['object'].data.splines[0].points] == before_white


def test_existing_display_translation_is_added_not_replaced(monkeypatch):
    collection, arrow, compound, plan, config = native_case(monkeypatch)
    arrow['object'].location.z = .0002
    result = apply_terminal_triangles([plan], collection, config)
    assert result[0]['display_z_offset_m'] == pytest.approx(.00025)
    assert arrow['object'].location.z == pytest.approx(.00045)


@pytest.mark.parametrize('change', ['unknown_order', 'later_order', 'alpha', 'point', 'spline', 'material'])
def test_native_compound_tampering_rejects_before_triangle_movement(monkeypatch, change):
    collection, arrow, compound, plan, config = native_case(monkeypatch)
    if change == 'unknown_order':
        compound['source_draw_order'] = None
    elif change == 'later_order':
        compound['source_draw_order'] = 10
    elif change == 'alpha':
        compound['fill_opacity'] = .2
    elif change == 'point':
        compound['object'].data.splines[0].points[0].co[0] = 99.
    elif change == 'spline':
        compound['object'].data.splines[0].use_cyclic_u = False
    else:
        compound['object'].data.materials[0].node_tree.nodes[0].inputs['Color'].default_value = (1., 1., 1., .2)
    with pytest.raises(ValueError, match='compound'):
        apply_terminal_triangles([plan], collection, config)
    assert arrow['object'].location.z == 0.


def test_missing_original_contour_ownership_does_not_claim_a_fix(monkeypatch):
    collection, arrow, compound, plan, config = native_case(monkeypatch)
    config['_source_compound_fill_objects'] = []
    result = apply_terminal_triangles([plan], collection, config)
    assert result[0]['status'] == 'unqualified'
    assert arrow['object'].location.z == 0.


def native_outline_case(monkeypatch):
    from test_image_paint_order_native import Object, Vec
    collection, arrow, compound, plan, config = native_case(monkeypatch)
    points = arrow['points_mm']
    outline = Object('source-outline', 'CURVE', NS(dimensions='3D', bevel_depth=.00002, extrude=0.,
        materials=arrow['object'].data.materials, splines=[NS(type='POLY', use_cyclic_u=True,
        bezier_points=[], points=[NS(co=Vec((x*.001, y*.001, 0., 1.))) for x, y in points])]))
    collection.all_objects.append(outline)
    config['_image_order_stroke_objects'] = {'arrow': [outline]}
    plan['outline'] = {'rgb': [0., 0., 0.], 'width_mm': .04, 'points_mm': points}
    xs, ys = zip(*points)
    plan['dependency_bounds_mm'] = [min(xs)-.02, min(ys)-.02, max(xs)+.02, max(ys)+.02]
    return collection, arrow, outline, plan, config


def test_native_combined_outline_moves_after_fill_without_rebuilding_source(monkeypatch):
    collection, arrow, outline, plan, config = native_outline_case(monkeypatch)
    before = [tuple(p.co) for p in outline.data.splines[0].points]
    result = apply_terminal_triangles([plan], collection, config)
    assert result[0]['status'] == 'applied' and result[0]['native_outline'] == outline.name
    assert outline.location.z - outline.data.bevel_depth > arrow['object'].location.z-.0004
    assert [tuple(p.co) for p in outline.data.splines[0].points] == before


@pytest.mark.parametrize('change', ['owner', 'width', 'color'])
def test_outline_ownership_width_or_color_cannot_be_borrowed(monkeypatch, change):
    collection, arrow, outline, plan, config = native_outline_case(monkeypatch)
    if change == 'owner':
        config['_image_order_stroke_objects'] = {}
    elif change == 'width':
        outline.data.bevel_depth *= 2
    else:
        # Do not mutate the separate source fill's material in this control.
        from test_fill_paint_order import emission_material
        outline.data.materials = [emission_material((1., 0., 0.))]
    if change == 'color':
        with pytest.raises(ValueError, match='outline.*material'):
            apply_terminal_triangles([plan], collection, config)
    else:
        assert apply_terminal_triangles([plan], collection, config)[0]['status'] == 'unqualified'
    assert arrow['object'].location.z == 0. and outline.location.z == 0.


def test_source_owned_following_line_keeps_geometry_and_finishes_above_arrow(monkeypatch):
    from test_image_paint_order_native import Object, Vec
    collection, arrow, outline, plan, config = native_outline_case(monkeypatch)
    points = [(1., 1.), (5., 1.)]
    line = Object('later-source-line', 'CURVE', NS(dimensions='3D', bevel_depth=.00002, extrude=0.,
        materials=outline.data.materials, splines=[NS(type='POLY', use_cyclic_u=False,
        bezier_points=[], points=[NS(co=Vec((x*.001, y*.001, 0., 1.))) for x, y in points])]))
    line['pdf_image_order_primitive_id'] = 'line'
    collection.all_objects.append(line)
    config['_image_order_stroke_objects']['line'] = [line]
    plan['later_strokes'] = [{'primitive_id': 'line', 'source_draw_order': 12,
        'points_mm': points, 'rgb': [0., 0., 0.], 'width_mm': .04, 'closed': False,
        'paint_bounds_mm': [.98, .98, 5.02, 1.02]}]
    plan['dependency_bounds_mm'][2] = max(plan['dependency_bounds_mm'][2], 5.02)
    before = [tuple(p.co) for p in line.data.splines[0].points]
    result = apply_terminal_triangles([plan], collection, config)
    assert result[0]['later_native_strokes'][0]['native_object'] == line.name
    assert line.location.z-.00002 > outline.location.z+.00002
    assert [tuple(p.co) for p in line.data.splines[0].points] == before


def compound_hole_source():
    outer = [(0., 0.), (10., 0.), (10., 10.), (0., 10.), (0., 0.)]
    inner = [(1., 1.), (9., 1.), (9., 9.), (1., 9.), (1., 1.)]
    clip = {'even_odd': True, 'items': [('l', a, b) for loop in (outer, inner)
                                      for a, b in zip(loop, loop[1:])]}
    original = {'seqno': 20, 'type': 'f', 'fill_opacity': 1., 'fill': (0., 0., 0.)}
    group = [NS(id=f'frame{i}', source_draw_order=20, fill_opacity=1.,
                source_fill_color=(0., 0., 0.), fill_color=(0., 0., 0.),
                clip_fill_even_odd=True, points=loop) for i, loop in enumerate((outer, inner))]
    return group, original, clip


def test_complete_source_evenodd_group_proves_hole_not_bbox_absence():
    from pdf_vector_importer.triangle_paint_order import _compound_disjoint_proof
    group, original, clip = compound_hole_source()
    proof = _compound_disjoint_proof(group, original, [clip], (2., 2., 4., 4.), lambda p: p)
    assert proof['source_draw_order'] == 20 and len(proof['contours']) == 2
    assert proof['source_clip']['winding'] == 2


@pytest.mark.parametrize('change', ['missing', 'moved', 'rule', 'duplicate_clip', 'island', 'order', 'alpha'])
def test_incomplete_or_painted_source_hole_cannot_exclude_compound(change):
    from pdf_vector_importer.triangle_paint_order import _compound_disjoint_proof
    group, original, clip = compound_hole_source()
    clips = [clip]
    if change == 'missing':
        group.pop()
    elif change == 'moved':
        group[1].points = [(x+.1, y) for x, y in group[1].points]
    elif change == 'rule':
        clip['even_odd'] = False
    elif change == 'duplicate_clip':
        clips.append(clip)
    elif change == 'island':
        loop = [(2.5, 2.5), (3., 2.5), (3., 3.), (2.5, 3.), (2.5, 2.5)]
        clip['items'] += [('l', a, b) for a, b in zip(loop, loop[1:])]
        group.append(NS(**{**vars(group[0]), 'id': 'island', 'points': loop}))
    elif change == 'order':
        group[1].source_draw_order = 21
    else:
        group[1].fill_opacity = .5
    assert _compound_disjoint_proof(group, original, clips, (2., 2., 4., 4.), lambda p: p) is None


def test_saved_pdf_later_compound_frame_does_not_block_earlier_arrow_in_its_hole(tmp_path):
    path = tmp_path/'later-frame.pdf'
    with fitz.open() as doc:
        page = doc.new_page(width=100, height=100)
        page.draw_rect((10, 10, 80, 80), color=None, fill=(1, 1, 1))
        page.draw_polyline([(25, 25), (35, 25), (30, 35)], closePath=True, color=None, fill=(0, 0, 0))
        xref = doc.get_new_xref()
        doc.update_object(xref, '<<>>')
        doc.update_stream(xref, b'q 5 5 m 95 5 l 95 95 l 50 96 l 5 95 l h '
                          b'15 15 m 85 15 l 85 85 l 50 86 l 15 85 l h W* n '
                          b'0 g 0 0 100 100 re f Q')
        doc.xref_set_key(page.xref, 'Contents', '['+' '.join(f'{ref} 0 R' for ref in (*page.get_contents(), xref))+']')
        doc.save(path)
    with fitz.open(path) as doc:
        data = extract_page(doc[0], 1, detect_arcs=False)
        white = next(p for p in data.primitives if p.source_draw_order == 0)
        white.clip_fill_group_id, white.clip_fill_even_odd = 'owned-white', True
        plans, unresolved = plan_terminal_triangles(doc[0], data, 'a'*64)
        assert unresolved == [] and len(plans) == 1
        group = plans[0]['disjoint_compound_groups'][0]
        assert group['source_draw_order'] == 2 and len(group['contours']) == 2


def native_frame_case(monkeypatch):
    from test_image_paint_order_native import Object, Vec
    from test_fill_paint_order import emission_material
    collection, arrow, compound, plan, config = native_case(monkeypatch)
    source, _, _ = compound_hole_source()

    class Point(Vec):
        def to_3d(self):
            return Vec(self[:3])

    frame = Object('later-frame', 'CURVE', NS(dimensions='2D', fill_mode='BOTH', bevel_depth=0., extrude=0.,
        materials=[emission_material((0., 0., 0.))], splines=[NS(type='POLY', use_cyclic_u=True,
            bezier_points=[], points=[NS(co=Point((x*.001, y*.001, 0., 1.))) for x, y in p.points[:-1]]) for p in source]))
    vertices = [NS(co=Vec((x*.001, y*.001, 0.))) for p in source for x, y in p.points[:-1]]
    indices = [(i, (i+1) % 4, 4+(i+1) % 4) for i in range(4)]
    indices += [(i, 4+(i+1) % 4, 4+i) for i in range(4)]
    mesh = NS(vertices=vertices, loop_triangles=[NS(vertices=t) for t in indices], calc_loop_triangles=lambda: None)
    frame.to_mesh = lambda: mesh
    frame.location.z = .02  # A high but disjoint frame must not inflate the arrow lift.
    collection.all_objects.append(frame)
    config['_source_compound_fill_objects'].append({'object': frame, 'primitive_ids': [p.id for p in source],
        'contours_mm': [p.points for p in source], 'source_draw_order': 20, 'fill_opacity': 1.,
        'fill_rgb': (0., 0., 0.), 'visual_style': 'source', 'even_odd': True})
    plan['disjoint_compound_groups'] = [{'source_draw_order': 20, 'contours': [
        {'primitive_id': p.id, 'source_draw_order': 20, 'points_mm': p.points,
         'fill_rgb': [0., 0., 0.], 'even_odd': True} for p in source]}]
    return collection, arrow, frame, mesh, plan, config


def test_actual_native_frame_triangles_preserve_hole_and_do_not_inflate_depth(monkeypatch):
    collection, arrow, frame, mesh, plan, config = native_frame_case(monkeypatch)
    result = apply_terminal_triangles([plan], collection, config)
    assert result[0]['status'] == 'applied'
    assert result[0]['disjoint_compound_readbacks'][0]['triangle_count'] == 8
    assert arrow['object'].location.z == pytest.approx(.00045)
    assert frame.location.z == .02 and frame.mesh_cleared


@pytest.mark.parametrize('change', ['paint_in_hole', 'touching_paint', 'no_faces', 'invalid_index', 'tilt', 'missing_contour', 'duplicate_owner'])
def test_evaluated_native_compound_absence_is_required_before_movement(monkeypatch, change):
    collection, arrow, frame, mesh, plan, config = native_frame_case(monkeypatch)
    if change == 'paint_in_hole':
        mesh.loop_triangles.append(NS(vertices=(4, 5, 6)))
    elif change == 'touching_paint':
        from test_image_paint_order_native import Vec
        mesh.vertices.extend(NS(co=Vec((x, y, 0.))) for x, y in ((.001, .002), (.002, .002), (.001, .003)))
        mesh.loop_triangles.append(NS(vertices=(8, 9, 10)))
    elif change == 'no_faces':
        mesh.loop_triangles.clear()
    elif change == 'invalid_index':
        mesh.loop_triangles[0].vertices = (0, 1, 88)
    elif change == 'tilt':
        mesh.vertices[0].co[2] = .001
    elif change == 'missing_contour':
        config['_source_compound_fill_objects'][-1]['primitive_ids'].pop()
        config['_source_compound_fill_objects'][-1]['contours_mm'].pop()
        frame.data.splines.pop()
    else:
        config['_source_compound_fill_objects'].append(config['_source_compound_fill_objects'][-1])
    with pytest.raises(ValueError, match='compound'):
        apply_terminal_triangles([plan], collection, config)
    assert arrow['object'].location.z == 0.
    if change != 'missing_contour':
        assert frame.mesh_cleared


def test_evaluated_compound_mesh_is_cleared_if_triangle_evaluation_raises(monkeypatch):
    from pdf_vector_importer.triangle_paint_order import _verify_evaluated_fill_disjoint
    _, _, frame, mesh, _, _ = native_frame_case(monkeypatch)
    def fail():
        raise RuntimeError('native triangulation failed')
    mesh.calc_loop_triangles = fail
    with pytest.raises(RuntimeError, match='triangulation'):
        _verify_evaluated_fill_disjoint(frame, None, (.002, .002, .004, .004))
    assert frame.mesh_cleared


def test_join_index_keeps_touching_unknown_and_invalid_envelopes_in_original_order():
    from pdf_vector_importer.triangle_paint_order import _JoinIndex
    rows = [(10., 0., 11., 1.), None, (2., 0., 3., 1.), (float('nan'), 0., 0., 0.),
            (4., 2., 3., 3.), (1., 0., 2., 1.)]
    index = _JoinIndex(rows, lambda row: row)
    # Inclusive boundary: the two valid near rows touch opposite query sides.
    assert index.candidates((2., 0., 2., 1.)) == tuple(rows[i] for i in (1, 2, 3, 4, 5))
    for unknown in (None, (), (0.,), (float('inf'), 0., 1., 1.), (2., 0., 1., 1.)):
        assert index.candidates(unknown) == tuple(rows)


@pytest.mark.parametrize('points,errors', [([], None), ([(1.,)], None), ([(1., 2.)], []),
    ([(1., 2.)], [(0., -1.)]), ([(float('inf'), 2.)], None), ([(1., 2.)], [(float('nan'), 0.)])])
def test_missing_or_invalid_match_budget_never_becomes_a_discardable_bound(points, errors):
    from pdf_vector_importer.triangle_paint_order import _match_bounds
    assert _match_bounds(points, errors) is None


def test_operand_cancellation_match_survives_index_without_weakening_exact_join():
    import struct
    from pdf_vector_importer.triangle_paint_order import _JoinIndex, _match_bounds
    f32 = lambda value: struct.unpack('f', struct.pack('f', value))[0]
    svg = '<svg><path transform="matrix(.12,0,0,-.12,0,1728)" d="M2000 14000L2050 14000L2025 14050Z"/></svg>'
    row = _svg_triangles(svg)[0]
    raw = [(f32(f32(.12)*x), f32(f32(f32(-.12)*y)+f32(1728)))
           for x, y in ((2000, 14000), (2050, 14000), (2025, 14050))]
    peers = [raw, [(x, y+.01) for x, y in raw], [(x+100., y) for x, y in raw], raw]
    index = _JoinIndex(peers, _match_bounds)
    candidates = index.candidates(_match_bounds(row['points'], row['point_errors']))
    assert len(candidates) < len(peers)
    assert [p for p in candidates if _source_points_match(row, p)] == [p for p in peers if _source_points_match(row, p)]
    assert len([p for p in candidates if _source_points_match(row, p)]) == 2  # ambiguity is retained


def test_exact_operand_tolerance_touch_survives_index_but_outside_still_fails():
    import math
    from pdf_vector_importer.triangle_paint_order import _JoinIndex, _match_bounds, _ulp32
    raw = [(0.5, 0.)]
    limit = .125 + 4*_ulp32(.5)
    row = {'points': [(0.5+limit, 0.)], 'point_errors': [(.125, 0.)]}
    index = _JoinIndex([raw], _match_bounds)
    assert _source_points_match(row, raw)
    assert index.candidates(_match_bounds(row['points'], row['point_errors'])) == (raw,)
    outside = {**row, 'points': [(math.nextafter(row['points'][0][0], math.inf), 0.)]}
    assert not _source_points_match(outside, raw)


@pytest.mark.parametrize('case', ['fill', 'outline', 'later_text', 'cloud', 'wide_cloud'])
def test_indexed_and_full_scan_saved_pdf_decisions_are_identical(tmp_path, monkeypatch, case):
    import pdf_vector_importer.triangle_paint_order as module
    def compare_same_source(page, data, source_sha):
        # Re-extraction allocates fresh global primitive IDs. Compare complete
        # plans from the SAME saved page and normalized objects, retaining all
        # source identities and proof fields in the equality assertion.
        indexed = module.plan_terminal_triangles(page, data, source_sha)
        with monkeypatch.context() as patch:
            patch.setattr(module._JoinIndex, 'candidates', lambda self, _box: self.rows)
            brute_force = module.plan_terminal_triangles(page, data, source_sha)
        assert brute_force == indexed
        return indexed
    if case in {'cloud', 'wide_cloud'}:
        cloud_case(tmp_path, width=40. if case == 'wide_cloud' else 2., planner=compare_same_source)
    else:
        source_case(tmp_path, outline=case == 'outline',
                    later='text' if case == 'later_text' else None, planner=compare_same_source)
