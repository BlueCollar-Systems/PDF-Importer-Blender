"""Source sheet view shows textures without changing previews or scene colors."""
import importlib
import base64
import sys
import types

import pytest

from test_bl_batch_origin_span import _reload_builder, _restore_replaced_modules  # noqa: F401


@pytest.fixture
def engine():
    _reload_builder()
    sys.modules.pop("pdf_vector_importer.bl_import_engine", None)
    return importlib.import_module("pdf_vector_importer.bl_import_engine")


def shading():
    return types.SimpleNamespace(type="SOLID", light="STUDIO", color_type="RANDOM",
        show_shadows=True, show_cavity=True, show_object_outline=True,
        show_specular_highlight=True)


def test_source_sheet_gets_flat_texture_view_and_no_lighting_masks(engine):
    value = shading()
    scene = types.SimpleNamespace(view_transform="User choice", exposure=2.5, gamma=0.8)
    engine.bpy.context = types.SimpleNamespace(scene=scene)
    engine._configure_sheet_view_shading(types.SimpleNamespace(shading=value), False, True)
    assert (value.type, value.light, value.color_type) == ("SOLID", "FLAT", "TEXTURE")
    assert not any(getattr(value, name) for name in (
        "show_shadows", "show_cavity", "show_object_outline", "show_specular_highlight"))
    assert vars(scene) == {"view_transform": "User choice", "exposure": 2.5, "gamma": 0.8}


@pytest.mark.parametrize("preview", [False, True])
def test_non_source_style_keeps_existing_handoff(engine, preview):
    value = shading()
    engine._configure_sheet_view_shading(types.SimpleNamespace(shading=value), preview, False)
    if preview:
        assert (value.type, value.light, value.color_type) == ("MATERIAL", "STUDIO", "TEXTURE")
        assert value.show_shadows is True
    else:
        assert (value.type, value.light, value.color_type) == ("SOLID", "FLAT", "MATERIAL")


def test_shader_required_source_paint_keeps_material_preview(engine):
    value = shading()
    engine._configure_sheet_view_shading(types.SimpleNamespace(shading=value), True, True)
    assert value.type == "MATERIAL"
    assert value.light == "STUDIO"


def image_object(kinds):
    class Image(dict):
        pass
    item = Image(pdf_image_path="fictional.png", pdf_image_workbench_opaque=True,
                 pdf_image_datablock="fictional packed image")
    class Nodes(list):
        active = None
    nodes = Nodes(types.SimpleNamespace(type=kind) for kind in kinds)
    by_type = {node.type: node for node in nodes}
    tex, bsdf, out = (by_type.get(key) for key in (
        "TEX_IMAGE", "BSDF_PRINCIPLED", "OUTPUT_MATERIAL"))
    links = []
    if tex is not None:
        tex.image = types.SimpleNamespace(name="fictional packed image",
                                         packed_file=types.SimpleNamespace(size=80))
        nodes.active = tex
    if bsdf is not None:
        bsdf.inputs = {name: types.SimpleNamespace(name=name, default_value=1.0)
                       for name in ("Alpha", "Specular", "Emission")}
    if tex is not None and bsdf is not None and out is not None:
        links = [types.SimpleNamespace(from_node=a, to_node=b,
                    from_socket=types.SimpleNamespace(name=a_socket),
                    to_socket=types.SimpleNamespace(name=b_socket))
                 for a, a_socket, b, b_socket in (
                     (tex, "Color", bsdf, "Emission"), (tex, "Alpha", bsdf, "Alpha"),
                     (bsdf, "BSDF", out, "Surface"))]
    item.active_material = types.SimpleNamespace(node_tree=types.SimpleNamespace(nodes=nodes, links=links))
    return item


def test_only_known_opaque_image_shader_takes_workbench_route(engine):
    opaque = image_object(["TEX_IMAGE", "BSDF_PRINCIPLED", "OUTPUT_MATERIAL"])
    assert engine._source_images_need_material_preview([opaque]) is False
    alpha = image_object(["TEX_IMAGE", "BSDF_PRINCIPLED", "OUTPUT_MATERIAL",
                          "BSDF_TRANSPARENT", "MIX_SHADER"])
    assert engine._source_images_need_material_preview([opaque, alpha]) is True
    unproven = image_object(["TEX_IMAGE", "EMISSION", "OUTPUT_MATERIAL"])
    assert engine._source_images_need_material_preview([unproven]) is True
    missing = image_object([])
    missing.active_material = None
    assert engine._source_images_need_material_preview([missing]) is True
    assert engine._source_images_need_material_preview([{}]) is False


@pytest.mark.parametrize("change", ["links", "alpha", "active", "packed", "binding", "opacity"])
def test_same_node_types_do_not_waive_unproven_alpha_or_binding(engine, change):
    item = image_object(["TEX_IMAGE", "BSDF_PRINCIPLED", "OUTPUT_MATERIAL"])
    tree = item.active_material.node_tree
    if change == "links":
        tree.links.clear()
    elif change == "alpha":
        next(n for n in tree.nodes if n.type == "BSDF_PRINCIPLED").inputs["Alpha"].default_value = 0.0
    elif change == "active":
        tree.nodes.active = None
    elif change == "packed":
        tree.nodes.active.image.packed_file = None
    elif change == "binding":
        item["pdf_image_datablock"] = "foreign image"
    else:
        item["pdf_image_workbench_opaque"] = False
    assert engine._source_images_need_material_preview([item]) is True


def test_png_opacity_proof_excludes_alpha_unknown_and_truncated_inputs(engine):
    rgb = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAgAAAAECAIAAAA8r+mnAAAAFklEQVR4nGP8z4AAjEgcJiRxFEC6BAC6kgIHlNolTgAAAABJRU5ErkJggg==")
    rgba = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAgAAAAECAYAAACzzX7wAAAAGUlEQVR4nGP8z4AKGP8zoAgxocljAMoVAABmsQMGGjeGeAAAAABJRU5ErkJggg==")
    assert engine._png_proven_opaque(rgb) is True
    assert engine._png_proven_opaque(rgba) is False
    assert engine._png_proven_opaque(rgb[:-4]) is False
    assert engine._png_proven_opaque(b"unproven JPEG") is False
    import zlib
    payload = b"tRNS" + b"\x00\x00\x00\x00\x00\x00"
    transparency_chunk = (6).to_bytes(4, "big") + payload + zlib.crc32(payload).to_bytes(4, "big")
    assert engine._png_proven_opaque(rgb[:-12] + transparency_chunk + rgb[-12:]) is False
    text_payload = b"tEXt" + b"fictional\x00bounded"
    text_chunk = ((len(text_payload) - 4).to_bytes(4, "big") + text_payload
                  + zlib.crc32(text_payload).to_bytes(4, "big"))
    assert engine._png_proven_opaque(rgb[:-12] + text_chunk * 4093 + rgb[-12:]) is True
    assert engine._png_proven_opaque(rgb[:-12] + text_chunk * 4094 + rgb[-12:]) is False


def test_old_api_without_texture_enum_retains_flat_material_view(engine):
    class OlderShading:
        type = "SOLID"
        light = "STUDIO"
        _color = "MATERIAL"
        @property
        def color_type(self):
            return self._color
        @color_type.setter
        def color_type(self, value):
            if value == "TEXTURE":
                raise ValueError("unsupported enum")
            self._color = value
    value = OlderShading()
    engine._configure_sheet_view_shading(types.SimpleNamespace(shading=value), False, True)
    assert (value.type, value.light, value.color_type) == ("SOLID", "FLAT", "MATERIAL")


def test_old_api_without_color_type_does_not_break_framing(engine):
    value = types.SimpleNamespace(type="SOLID", light="STUDIO")
    engine._configure_sheet_view_shading(types.SimpleNamespace(shading=value), False, True)
    assert (value.type, value.light) == ("SOLID", "FLAT")
    assert not hasattr(value, "color_type")
