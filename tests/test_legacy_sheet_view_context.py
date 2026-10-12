"""Real legacy dictionary convention, without global context or app changes."""
from contextlib import contextmanager
import importlib
import sys
import types

import pytest

from test_bl_batch_origin_span import _reload_builder, _restore_replaced_modules, _Vector  # noqa: F401


@pytest.fixture
def engine():
    _reload_builder()
    sys.modules.pop("pdf_vector_importer.bl_import_engine", None)
    return importlib.import_module("pdf_vector_importer.bl_import_engine")


def environment(engine, *, modern, bounds=True, local=False, selected_fails=False):
    class Drawing(dict):
        type = "CURVE"
        name = "Fictional drawing"
        selected = False
        def select_set(self, value):
            self.selected = value
        def hide_set(self, _value):
            pass
    class Objects(list):
        active = None
    draw = Drawing()
    scene = types.SimpleNamespace(view_settings={"transform": "user choice"})
    layer = types.SimpleNamespace(objects=Objects([draw]))
    areas = [types.SimpleNamespace(type="VIEW_3D", regions=[types.SimpleNamespace(type="WINDOW")],
        spaces=types.SimpleNamespace(active=types.SimpleNamespace(region_3d=object(),
            local_view=object() if local else None, use_local_collections=True,
            clip_start=0.01, clip_end=20.0, shading=types.SimpleNamespace(
                type="SOLID", light="STUDIO", color_type="MATERIAL")))) for _ in range(2)]
    screen = types.SimpleNamespace(areas=areas)
    window = types.SimpleNamespace(screen=screen)
    original = {"sentinel": "real context copy", "area": "file browser", "space_data": "old space"}
    calls, scopes = [], []
    context = types.SimpleNamespace(scene=scene, view_layer=layer,
        window_manager=types.SimpleNamespace(windows=[window]), copy=lambda: dict(original))
    if modern:
        @contextmanager
        def temporary(**values):
            scopes.append(values)
            yield
        context.temp_override = temporary
    def operator(name):
        def invoke(*args, **kwargs):
            assert len(args) == (0 if modern else 1)
            override = scopes[-1] if modern else args[0]
            assert override["window"] is window and override["screen"] is screen
            assert override["region"] in override["area"].regions
            assert override["scene"] is scene and override["view_layer"] is layer
            if not modern:
                assert type(override) is dict
                assert override["sentinel"] == "real context copy"
                assert override["space_data"] is override["area"].spaces.active
                assert override["region_data"] is override["area"].spaces.active.region_3d
            calls.append((name, override["area"], kwargs))
            if name == "localview":
                override["area"].spaces.active.local_view = None
            if name == "view_selected" and selected_fails:
                raise RuntimeError("selected poll failed")
            return {"FINISHED"}
        return invoke
    engine.bpy.context = context
    engine.bpy.ops = types.SimpleNamespace(view3d=types.SimpleNamespace(
        localview=operator("localview"), view_selected=operator("view_selected"), view_all=operator("view_all")))
    return draw, scene, layer, areas, window, original, calls, scopes


@pytest.mark.parametrize("modern", [False, True])
@pytest.mark.parametrize("keep", [False, True])
def test_local_view_and_direct_frame_preserve_context_selection_clips(engine, monkeypatch, modern, keep):
    draw, scene, layer, areas, _window, original, calls, scopes = environment(engine, modern=modern, local=True)
    frames = []
    monkeypatch.setattr(engine, "_unhide_collection_tree", lambda _root: None)
    monkeypatch.setattr(engine, "_import_view_bounds", lambda *_args: (_Vector((0, 0, 0)), _Vector((2, 10, 0)), "test"))
    monkeypatch.setattr(engine, "_frame_sheet_view", lambda rv, *_args: frames.append(rv) or True)
    assert engine._focus_view_on_import(types.SimpleNamespace(all_objects=[draw]),
        keep_selected=keep, source_texture_view=True) is True
    assert frames == [a.spaces.active.region_3d for a in areas]
    assert [name for name, _area, _kwargs in calls] == ["localview", "localview"]
    assert draw.selected is keep
    assert layer.objects.active is (draw if keep else None)
    assert scene.view_settings == {"transform": "user choice"}
    assert original == {"sentinel": "real context copy", "area": "file browser", "space_data": "old space"}
    assert all(not a.spaces.active.use_local_collections for a in areas)
    assert all(a.spaces.active.clip_end >= 1000 and a.spaces.active.clip_start <= 0.01 for a in areas)
    assert all((a.spaces.active.shading.type, a.spaces.active.shading.light,
                a.spaces.active.shading.color_type) == ("SOLID", "FLAT", "TEXTURE") for a in areas)
    if modern:
        assert all(set(s) == {"window", "screen", "area", "region", "scene", "view_layer"} for s in scopes)


@pytest.mark.parametrize("modern", [False, True])
def test_missing_bounds_selected_then_all_uses_supported_operator_context(engine, monkeypatch, modern):
    draw, _scene, _layer, areas, _window, _original, calls, _scopes = environment(
        engine, modern=modern, selected_fails=True)
    monkeypatch.setattr(engine, "_unhide_collection_tree", lambda _root: None)
    monkeypatch.setattr(engine, "_import_view_bounds", lambda *_args: (None, None, "unknown"))
    monkeypatch.setattr(engine, "_frame_sheet_view", lambda *_args: False)
    assert engine._focus_view_on_import(types.SimpleNamespace(all_objects=[draw])) is True
    assert [name for name, _area, _kwargs in calls] == ["view_selected", "view_selected", "view_all", "view_all"]
    assert [area for _name, area, _kwargs in calls] == areas + areas
    assert calls[0][2] == {"use_all_regions": False} and calls[-1][2] == {"center": False}


def test_modern_context_failure_is_not_retried_as_legacy(engine):
    _draw, _scene, layer, areas, window, _original, _calls, _scopes = environment(engine, modern=True)
    engine.bpy.context.temp_override = lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("modern failure"))
    engine.bpy.context.copy = lambda: pytest.fail("modern exceptions must not switch API")
    with pytest.raises(RuntimeError, match="modern failure"):
        with engine._sheet_view_context(window, window.screen, areas[0], areas[0].regions[0], layer):
            pytest.fail("failing scope entered")
