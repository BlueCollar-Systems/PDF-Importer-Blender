"""Source order, overlap and conservative unknown-fill controls."""
import copy
import sys
from types import SimpleNamespace as NS

import pytest

from pdf_vector_importer.fill_paint_order import apply_fill_depths, fill_depth_plan, same_native_object


def face(identity, seqno, box=(0., 0., 1., 1.), opaque=True):
    return {'id': identity, 'seqno': seqno, 'bounds': box, 'opaque': opaque}


def offsets(records):
    result = fill_depth_plan(records)
    return {row['id']: row['offset_m'] for component in result
            if component['status'] == 'qualified' for row in component['rows']}


def test_earlier_white_later_black_overlap_preserves_points_and_source_order():
    records = [face('white', 4), face('black', 9, (.2, .2, .4, .4))]
    before = copy.deepcopy(records)
    result = offsets(records)
    assert result['white'] == 0
    assert result['black'] == 0.00005
    assert records == before


def test_opposite_paint_order_places_late_white_over_black():
    result = offsets([face('white', 9), face('black', 4)])
    assert result['white'] > result['black']


def test_disjoint_faces_do_not_receive_artificial_depth_ranks():
    assert fill_depth_plan([face('a', 1), face('b', 2, (2., 2., 3., 3.))]) == []


@pytest.mark.parametrize('bad', [None, True, -1, 2.5])
def test_unknown_order_blocks_entire_overlap_component(bad):
    result = fill_depth_plan([face('a', 1), face('b', bad)])
    assert result == [{'status': 'unqualified', 'reason': 'unknown_or_nonopaque_overlapping_fill', 'ids': ['a', 'b']}]


def test_nonopaque_overlap_is_not_silently_reordered():
    result = fill_depth_plan([face('a', 1), face('b', 2, opaque=False)])
    assert result[0]['status'] == 'unqualified'


def test_only_dependency_paths_add_depth_and_display_budget_is_bounded():
    rows = [face('a', 10), face('b', 20, (.5, 0., 1.5, 1.)), face('c', 30, (1.25, 0., 2., 1.)),
            face('disjoint', 999, (3., 0., 4., 1.))]
    result = offsets(rows)
    assert result == {'a': 0., 'b': .000025, 'c': .00005}


def test_same_original_paint_does_not_invent_subpath_order():
    assert fill_depth_plan([face('a', 1), face('b', 1)]) == []


def test_duplicate_identity_and_invalid_budget_reject():
    with pytest.raises(ValueError, match='Duplicate'):
        fill_depth_plan([face('a', 1), face('a', 2)])
    with pytest.raises(ValueError, match='budget'):
        fill_depth_plan([face('a', 1)], 1.0)


def test_native_rna_identity_allows_alias_wrapper_but_not_equal_custom_properties():
    first, alias, other = (NS(as_pointer=lambda: 5), NS(as_pointer=lambda: 5), NS(as_pointer=lambda: 6))
    assert same_native_object(first, alias)
    assert not same_native_object(first, other)
    assert not same_native_object({}, {})


def native_faces(monkeypatch):
    from test_image_paint_order_native import Object, Vec
    monkeypatch.setitem(sys.modules, 'mathutils', NS(Vector=Vec))
    monkeypatch.setitem(sys.modules, 'bpy', NS(context=NS(view_layer=NS(update=lambda: None),
                                                        evaluated_depsgraph_get=lambda: None)))
    objects, owned = [], []
    for identity, seq, rgb, points in (
        ('white', 4, (1., 1., 1.), [(0., 0.), (10., 0.), (10., 10.), (0., 10.)]),
        ('arrow', 9, (0., 0., 0.), [(2., 2.), (4., 2.), (3., 4.)]),
    ):
        material = emission_material(rgb)
        obj = Object(identity, 'MESH', NS(
            vertices=[NS(co=Vec((x*.001, y*.001, -.0004))) for x, y in points],
            edges=[], polygons=[NS(vertices=tuple(range(len(points))), material_index=0)],
            uv_layers=NS(active=None), materials=[material],
        ))
        objects.append(obj)
        owned.append({'object': obj, 'primitive_id': identity, 'source_draw_order': seq,
                      'points_mm': points, 'fill_rgb': rgb, 'fill_opacity': 1., 'visual_style': 'source'})
    return NS(all_objects=objects), owned


def emission_material(rgb):
    emission = NS(type='EMISSION', inputs={'Color': NS(default_value=(*rgb, 1.)),
                                         'Strength': NS(default_value=1)},
                  outputs={'Emission': object()})
    output = NS(type='OUTPUT_MATERIAL', inputs={'Surface': object()})
    return NS(diffuse_color=(*rgb, 1.), use_nodes=True,
              node_tree=NS(nodes=[emission, output], links=[NS(
                  from_socket=emission.outputs['Emission'], to_socket=output.inputs['Surface'])]))


def test_native_display_move_keeps_exact_local_vertices_and_source_xy(monkeypatch):
    collection, owned = native_faces(monkeypatch)
    before = [[tuple(v.co) for v in row['object'].data.vertices] for row in owned]
    result = apply_fill_depths(collection, owned)
    assert result[0]['status'] == 'qualified'
    assert owned[0]['object'].location.z == 0.
    assert owned[1]['object'].location.z == .00005
    assert [[tuple(v.co) for v in row['object'].data.vertices] for row in owned] == before
    assert all(tuple(row['object'].location[:2]) == (0., 0.) for row in owned)


def test_wrong_native_color_rejects_before_any_display_move(monkeypatch):
    collection, owned = native_faces(monkeypatch)
    owned[1]['object'].data.materials[0].diffuse_color = (1., 0., 0., 1.)
    with pytest.raises(ValueError, match='material differs'):
        apply_fill_depths(collection, owned)
    assert all(row['object'].location.z == 0. for row in owned)


def test_unknown_native_source_order_retains_component_and_reports_it(monkeypatch):
    collection, owned = native_faces(monkeypatch)
    owned[1]['source_draw_order'] = None
    assert apply_fill_depths(collection, owned)[0]['status'] == 'unqualified'
    assert all(row['object'].location.z == 0. for row in owned)


def test_actual_emission_alpha_cannot_differ_from_display_color(monkeypatch):
    collection, owned = native_faces(monkeypatch)
    owned[1]['object'].data.materials[0].node_tree.nodes[0].inputs['Color'].default_value = (0., 0., 0., .2)
    with pytest.raises(ValueError, match='actual emission'):
        apply_fill_depths(collection, owned)
    assert all(row['object'].location.z == 0. for row in owned)


@pytest.mark.parametrize('loop', [(0, 1, 2), (0, 2, 1, 3)])
def test_dropped_or_rewired_polygon_loop_rejects_before_movement(monkeypatch, loop):
    collection, owned = native_faces(monkeypatch)
    owned[0]['object'].data.polygons[0].vertices = loop
    with pytest.raises(ValueError, match='complete source loop'):
        apply_fill_depths(collection, owned)
    assert all(row['object'].location.z == 0. for row in owned)


def test_tilted_actual_world_plane_is_not_accepted_as_a_source_mask(monkeypatch):
    from test_image_paint_order_native import Translation
    collection, owned = native_faces(monkeypatch)
    obj = owned[0]['object']

    class Tilt(Translation):
        def __matmul__(self, point):
            result = super().__matmul__(point)
            result.z += result.x * .1
            return result

    obj.matrix_world = Tilt(obj)
    with pytest.raises(ValueError, match='footprint'):
        apply_fill_depths(collection, owned)
    assert all(row['object'].location.z == 0. for row in owned)
