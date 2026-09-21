import pytest
import sys
from types import SimpleNamespace as NS

from pdf_vector_importer.page_background import background_spec


def test_faithful_white_background_is_default_source_only_and_not_source_item():
    result = background_spec(914.4, 609.6, [-.003, .01])
    assert result['points_mm'] == [(0., 0.), (914.4, 0.), (914.4, 609.6), (0., 609.6)]
    assert result['depth_m'] == pytest.approx(-.00305)
    assert result['source_item'] is False
    assert result['role'] == 'display_only_page_background'


@pytest.mark.parametrize('style', ['blueprint', 'high_contrast'])
def test_dark_preview_palettes_do_not_get_white_backing(style):
    assert background_spec(1., 2., [0.], style=style) is None


def test_user_can_disable_without_any_background_geometry():
    assert background_spec(1., 2., [0.], enabled=False) is None


def test_no_ink_page_background_remains_behind_source_plane():
    assert background_spec(1., 2., [])['depth_m'] < 0


@pytest.mark.parametrize('width,height,bottoms', [(0, 1, []), (1, -1, []), (float('nan'), 1, []), (1, 1, [float('inf')])])
def test_invalid_dimensions_or_source_depth_are_not_guessed(width, height, bottoms):
    with pytest.raises(ValueError):
        background_spec(width, height, bottoms)


def test_actual_background_has_page_extent_and_sits_below_source_without_changing_it(monkeypatch):
    from test_fill_paint_order import native_faces, emission_material
    from test_image_paint_order_native import Object, Vec
    from pdf_vector_importer.page_background import add_page_background

    collection, _ = native_faces(monkeypatch)
    before = [(obj.name, tuple(obj.location), [tuple(v.co) for v in obj.data.vertices])
              for obj in collection.all_objects]

    def face(name, points, target, material, z_offset_m):
        obj = Object(name, 'MESH', NS(vertices=[NS(co=Vec((x*.001, y*.001, z_offset_m)))
                                              for x, y in points],
                                     polygons=[NS(vertices=(0, 1, 2, 3), material_index=0)],
                                     edges=[], uv_layers=NS(active=None), materials=[material]))
        target.all_objects.append(obj)
        return obj

    monkeypatch.setitem(sys.modules, 'pdf_vector_importer.bl_geometry_builder', NS(
        _create_face_mesh=face, _get_or_create_material=lambda rgb, cache, style: emission_material(rgb)))
    result = add_page_background(collection, 100., 50.)
    paper = collection.all_objects[-1]
    assert result['source_item'] is False
    assert result['individually_hideable'] is True
    assert paper.hide_select is True
    assert [(v.co.x, v.co.y) for v in paper.data.vertices] == [(0., 0.), (.1, 0.), (.1, .05), (0., .05)]
    assert max(v.co.z for v in paper.data.vertices) < -.0004
    assert [(obj.name, tuple(obj.location), [tuple(v.co) for v in obj.data.vertices])
            for obj in collection.all_objects[:-1]] == before
