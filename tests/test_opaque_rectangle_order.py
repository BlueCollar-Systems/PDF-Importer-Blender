"""Independent source joins and retained parent-affine display contracts."""
import copy
from types import SimpleNamespace as NS

import pytest

from pdf_vector_importer.opaque_rectangle_order import (
    _matrix_values, _native_text_for_plan, _translate_owned_text, bind_rectangle_plans,
    apply_rectangle_order,
)


def source_case():
    factor = 25.4 / 72
    quad = [(10., 20.), (30., 20.), (30., 40.), (10., 40.)]
    primitive = NS(id=7, source_draw_order=2, type='rect', clip_fill_group_id=None,
                   fill_opacity=1., source_fill_color=(1., 1., 1.),
                   points=[(x * factor, (100-y) * factor) for x, y in quad],
                   dash_pattern=None, stroke_opacity=1., source_stroke_color=(1., 0., 0.),
                   line_width=factor)
    item = NS(id=9, text='A', source_bbox_pdf=(12., 22., 18., 28.),
              source_char_layout=[NS(text='A', source_origin_pdf=(12., 27.))])
    proof = {'source_draw_order': 2, 'source_quad_pdf': quad, 'fill_rgb': [1., 1., 1.],
             'stroke_rgb': [1., 0., 0.], 'stroke_width': 1.,
             'paint_bounds_pdf': [9.5, 19.5, 30.5, 40.5],
             'later_text': [{'source_paint_order': 4, 'items': [
                 {'source_item_id': 'p1:b42:l0:s0', 'text': 'A',
                  'bbox_pdf': list(item.source_bbox_pdf), 'chars': [(65, (12., 27.))]}]}]}
    page = NS(page_number=1, primitives=[primitive], text_items=[item])
    return proof, page


def test_source_coordinates_bind_numeric_ids_without_assuming_rawdict_id_scheme():
    proof, page = source_case()
    before = copy.deepcopy(proof)
    plans, unresolved = bind_rectangle_plans([proof], page, (0, 0, 100, 100))
    assert unresolved == []
    assert plans[0]['primitive_id'] == 7
    assert plans[0]['later_text'] == [{'seqno': 4, 'span_ids': [9]}]
    assert proof == before


@pytest.mark.parametrize('change', ['point', 'color', 'alpha', 'ambiguous_primitive', 'ambiguous_text', 'text_origin'])
def test_source_binding_rejects_tampered_or_ambiguous_occurrences(change):
    proof, page = source_case()
    primitive, item = page.primitives[0], page.text_items[0]
    if change == 'point':
        primitive.points[0] = (100., 100.)
    elif change == 'color':
        primitive.source_fill_color = (0., 0., 0.)
    elif change == 'alpha':
        primitive.fill_opacity = .2
    elif change == 'ambiguous_primitive':
        page.primitives.append(copy.deepcopy(primitive))
    elif change == 'ambiguous_text':
        page.text_items.append(copy.deepcopy(item))
    else:
        item.source_char_layout[0].source_origin_pdf = (13., 27.)
    plans, unresolved = bind_rectangle_plans([proof], page, (0, 0, 100, 100))
    assert not plans
    assert unresolved[0]['status'] == 'unqualified'


class Matrix(list):
    def copy(self):
        return Matrix([list(row) for row in self])


def matrix():
    return Matrix([[1.2, .3, 0., 4.], [.2, .8, 0., 5.], [0., 0., 1., .01], [0., 0., 0., 1.]])


class Obj(dict):
    def __init__(self, name='glyph'):
        super().__init__()
        self.name, self.type, self.parent = name, 'FONT', None
        self.modifiers, self.constraints = [], []
        self.data = NS(body='A')
        self.matrix_world = matrix()
        self.matrix_basis, self.matrix_parent_inverse = matrix(), matrix()
        self.children = []
        self.hide_render = False


def test_native_text_move_preserves_shear_axes_xy_and_actual_data_identity():
    obj = Obj()
    before, data = _matrix_values(obj.matrix_world), obj.data
    _translate_owned_text(obj, .025)
    after = _matrix_values(obj.matrix_world)
    assert after[2][3] == pytest.approx(before[2][3] + .025)
    for r in range(4):
        for c in range(4):
            if (r, c) != (2, 3):
                assert after[r][c] == before[r][c]
    assert obj.data is data


def test_parented_affine_moves_only_owned_carrier_and_keeps_child_basis_exact():
    obj, carrier = Obj(), Obj('carrier')
    carrier.type = 'EMPTY'
    carrier.children = [obj]
    obj.parent = carrier
    obj.update(pdf_affine_carrier_owned=True, pdf_affine_carrier=carrier.name)
    basis, inverse, child_world = (_matrix_values(obj.matrix_basis),
                                   _matrix_values(obj.matrix_parent_inverse), _matrix_values(obj.matrix_world))
    _translate_owned_text(obj, .025)
    assert carrier.matrix_world[2][3] == pytest.approx(.035)
    assert _matrix_values(obj.matrix_basis) == basis
    assert _matrix_values(obj.matrix_parent_inverse) == inverse
    assert _matrix_values(obj.matrix_world) == child_world
    assert obj.parent is carrier


def test_shared_or_unowned_parent_rejects_before_source_transform_changes():
    obj, carrier = Obj(), Obj('carrier')
    carrier.type = 'EMPTY'
    carrier.children = [obj, Obj('unrelated')]
    obj.parent = carrier
    obj.update(pdf_affine_carrier_owned=True, pdf_affine_carrier=carrier.name)
    before = _matrix_values(carrier.matrix_world)
    with pytest.raises(ValueError, match='carrier ownership'):
        _translate_owned_text(obj, .1)
    assert _matrix_values(carrier.matrix_world) == before


def text_delivery():
    obj = Obj()
    obj.update(pdf_source_span_id=9, pdf_source_item_id='page:1:text:9', pdf_text_mode='3d_text')
    record = {'page': 1, 'source_span_id': 9, 'item_id': 'page:1:text:9', 'status': 'delivered',
              'entity_ids': ['glyph'], 'final_representation': '3d_text'}
    plan = {'page': 1, 'later_text': [{'seqno': 4, 'span_ids': [9]}]}
    return obj, record, plan


def test_native_text_delivery_is_used_without_switching_representation():
    obj, record, plan = text_delivery()
    assert _native_text_for_plan(plan, [record], {'glyph': obj}) == [(4, [obj])]
    assert obj['pdf_text_mode'] == '3d_text'


def test_hidden_or_extra_owned_text_is_not_silently_omitted():
    obj, record, plan = text_delivery()
    obj.hide_render = True
    with pytest.raises(ValueError, match='visibility'):
        _native_text_for_plan(plan, [record], {'glyph': obj})
    obj.hide_render = False
    other = Obj('omitted')
    other.update(obj)
    with pytest.raises(ValueError, match='incomplete'):
        _native_text_for_plan(plan, [record], {'glyph': obj, 'omitted': other})


def test_failed_text_delivery_leaves_mask_unqualified():
    obj, record, plan = text_delivery()
    record['status'] = 'failed'
    assert _native_text_for_plan(plan, [record], {'glyph': obj}) is None


def native_mask_case(monkeypatch):
    from test_fill_paint_order import native_faces
    from test_image_paint_order_native import Object, Vec
    from pdf_vector_importer import opaque_rectangle_order as consumer

    collection, fills = native_faces(monkeypatch)
    text = Object('glyph', 'FONT', NS(body='A', splines=[NS(
        points=[NS(co=Vec((.002, .002, .004, 1))), NS(co=Vec((.004, .004, .005, 1)))])]))
    text.update(pdf_source_span_id=9, pdf_source_item_id='page:1:text:9', pdf_text_mode='3d_text')
    collection.all_objects.append(text)
    _, delivery, _ = text_delivery()
    plan = {'page': 1, 'primitive_id': 'white', 'fill_rgb': [1., 1., 1.],
            'source_proof': {'source_draw_order': 4}, 'has_border': False,
            'dependency_bounds_mm': [0., 0., 10., 10.],
            'later_text': [{'seqno': 7, 'span_ids': [9]}]}
    # The affine mover has separate parent/shear/error tests. Here the host
    # double evaluates real vertex locations to test compositing order/closure.
    monkeypatch.setattr(consumer, '_translate_owned_text',
                        lambda obj, offset: setattr(obj.location, 'z', obj.location.z + offset))
    return collection, fills, text, delivery, plan


def test_actual_mask_rises_above_earlier_ink_then_native_3d_text_clears_mask(monkeypatch):
    collection, fills, text, delivery, plan = native_mask_case(monkeypatch)
    before = {obj.name: [tuple(v.co) for v in obj.data.vertices]
              for obj in collection.all_objects if obj.type == 'MESH'}
    result = apply_rectangle_order([plan], collection, {'_source_fill_objects': fills}, [delivery])
    assert result[0]['status'] == 'applied'
    mask_z = fills[0]['object'].location.z - .0004
    text_z = text.location.z + .004
    assert text_z > mask_z > -.0004
    assert mask_z == pytest.approx(-.00035)
    assert text['pdf_text_mode'] == '3d_text'
    assert {obj.name: [tuple(v.co) for v in obj.data.vertices]
            for obj in collection.all_objects if obj.type == 'MESH'} == before
    assert all(tuple(obj.location[:2]) == (0., 0.) for obj in collection.all_objects)


def test_unaccounted_native_glyph_extent_retains_mask_without_movement(monkeypatch):
    collection, fills, text, delivery, plan = native_mask_case(monkeypatch)
    text.data.splines[0].points[-1].co[0] = .020
    result = apply_rectangle_order([plan], collection, {'_source_fill_objects': fills}, [delivery])
    assert result[0]['reason'] == 'native_mask_paint_exceeds_source_dependency_bounds'
    assert all(obj.location.z == 0 for obj in collection.all_objects)


def test_a_disjoint_tall_model_does_not_push_source_mask_to_unrelated_height(monkeypatch):
    from test_image_paint_order_native import Object, Vec
    collection, fills, _, delivery, plan = native_mask_case(monkeypatch)
    distant = Object('distant', 'MESH', NS(vertices=[NS(co=Vec((10., 10., 100.)))],
                                        edges=[], polygons=[], uv_layers=NS(active=None)))
    collection.all_objects.append(distant)
    result = apply_rectangle_order([plan], collection, {'_source_fill_objects': fills}, [delivery])
    assert result[0]['status'] == 'applied'
    assert result[0]['final_display_top_m'] < .01


def test_own_future_text_extrusion_does_not_raise_mask_above_itself(monkeypatch):
    collection, fills, text, delivery, plan = native_mask_case(monkeypatch)
    text.data.splines[0].points[0].co[2] = -.003
    text.data.splines[0].points[1].co[2] = .003
    result = apply_rectangle_order([plan], collection, {'_source_fill_objects': fills}, [delivery])
    assert result[0]['status'] == 'applied'
    assert fills[0]['object'].location.z-.0004 == pytest.approx(-.00035)
    assert text.location.z-.003 == pytest.approx(-.00030)
    assert text.location.z+.003 == pytest.approx(.00570)
