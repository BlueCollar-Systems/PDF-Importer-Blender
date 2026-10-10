"""Nearby legitimate PDF paints retain their own native materials."""
import sys
import types

import pytest

if "bpy" not in sys.modules:
    sys.modules["bpy"] = types.SimpleNamespace()
if not hasattr(sys.modules["bpy"], "app"):
    sys.modules["bpy"].app = types.SimpleNamespace(version=(4, 1, 0))
if not hasattr(sys.modules["bpy"], "types"):
    sys.modules["bpy"].types = types.SimpleNamespace(Collection=object, Object=object)
if "bmesh" not in sys.modules:
    sys.modules["bmesh"] = types.SimpleNamespace()

from pdf_vector_importer import bl_geometry_builder as builder  # noqa: E402
from pdf_vector_importer.visual_style import preview_color, scene_linear_color  # noqa: E402


class Nodes(list):
    def new(self, *, type):
        node = types.SimpleNamespace(
            inputs={name: types.SimpleNamespace(default_value=None)
                    for name in ("Color", "Strength", "Surface")},
            outputs={"Emission": object()})
        self.append(node)
        return node


@pytest.mark.parametrize("style", ["source", "blueprint", "high_contrast"])
def test_different_stroke_and_fill_program_values_keep_source_style(monkeypatch, style):
    # Distinct RGB values delivered by independent PDF stroke/fill conversion.
    stroke = (.13725490868091583, .12156862765550613, .125490203499794)
    fill = (.13723964989185333, .12156862765550613, .1254749298095703)
    assert tuple(round(v, 3) for v in stroke) == tuple(round(v, 3) for v in fill)
    created = []

    def new_material(*, name):
        material = types.SimpleNamespace(name=name, node_tree=types.SimpleNamespace(
            nodes=Nodes(), links=types.SimpleNamespace(new=lambda *_args: None)))
        created.append(material)
        return material

    monkeypatch.setattr(builder.bpy, "data", types.SimpleNamespace(
        materials=types.SimpleNamespace(new=new_material)), raising=False)
    cache = {}
    line_material = builder._get_or_create_material(stroke, cache, style)
    fill_material = builder._get_or_create_material(fill, cache, style)
    assert line_material is not fill_material
    assert line_material.diffuse_color == (*scene_linear_color(preview_color(stroke, style)), 1.0)
    assert fill_material.diffuse_color == (*scene_linear_color(preview_color(fill, style)), 1.0)
    assert fill_material.node_tree.nodes[0].inputs["Color"].default_value == fill_material.diffuse_color
    assert builder._get_or_create_material(fill, cache, style) is fill_material
    assert len(created) == 2
