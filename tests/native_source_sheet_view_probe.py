"""Opt-in background bpy view/asset persistence proof; never desktop acceptance.

Run with --factory-startup --background --python this_file -- SOURCE OUT RGB RGBA.
OUT must be a new owned directory. Inputs are fictional PNG controls.
"""
from pathlib import Path
import hashlib
import json
import sys

import bpy

source, output, rgb_path, rgba_path = map(Path, sys.argv[sys.argv.index("--") + 1:])
assert not output.exists()
output.mkdir()
sys.path.insert(0, str(source))
from pdf_vector_importer import bl_import_engine as engine
assert Path(engine.__file__).resolve().is_relative_to(source.resolve())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def scene_color():
    value = bpy.context.scene.view_settings
    return {key: getattr(value, key) for key in ("view_transform", "look", "exposure", "gamma")}


def view_spaces():
    return [area.spaces.active for screen in bpy.data.screens
            for area in screen.areas if area.type == "VIEW_3D"]


def image_state(obj):
    material = obj.active_material
    active = material.node_tree.nodes.active
    assert active is not None and active.type == "TEX_IMAGE" and active.image is not None
    assert obj.data.uv_layers.active is not None
    return {
        "active_texture_node": active.type,
        "shader_types": sorted(node.type for node in material.node_tree.nodes),
        "uv_name": obj.data.uv_layers.active.name,
        "uv": [list(item.uv) for item in obj.data.uv_layers.active.data],
        "packed_sha256": hashlib.sha256(bytes(active.image.packed_file.data)).hexdigest(),
        "image_size": list(active.image.size), "image_colorspace": active.image.colorspace_settings.name,
        "source_png_proven_opaque": bool(obj.get("pdf_image_workbench_opaque", False)),
    }


colors_before = scene_color()
root = bpy.data.collections.new("Fictional view control")
bpy.context.scene.collection.children.link(root)
objects = {}
cache = engine._ImportImageCache()
for name, path, image_cache in (
    ("rgb_uncached", rgb_path, None), ("rgb_cached", rgb_path, cache),
    ("rgba_cached", rgba_path, cache),
):
    obj = engine._create_image_plane({"path": str(path), "page_number": 1, "xref": len(objects) + 1,
                                    "width_mm": 20, "height_mm": 10}, root,
                                   image_cache=image_cache)
    assert obj is not None, name
    obj.name = name
    objects[name] = obj
    assert image_state(obj)["packed_sha256"] == digest(path)
cache.release()
assert engine._source_images_need_material_preview([objects["rgb_uncached"], objects["rgb_cached"]]) is False
assert engine._source_images_need_material_preview([objects["rgba_cached"]]) is True

# A real curve without a texture retains its material display color in texture
# mode; the official Workbench fallback is visually checked separately by Root.
curve = bpy.data.curves.new("Fictional source red", "CURVE")
curve.dimensions = "2D"
spline = curve.splines.new("POLY")
spline.points.add(3)
spline.points.foreach_set("co", [0, 0, 0, 1, .01, 0, 0, 1, .01, .01, 0, 1, 0, .01, 0, 1])
spline.use_cyclic_u = True
obj = bpy.data.objects.new("Fictional red curve", curve)
root.objects.link(obj)
material = bpy.data.materials.new("Fictional red material")
material.diffuse_color = (1, 0, 0, 1)
curve.materials.append(material)
assert engine._source_images_need_material_preview([obj]) is False

spaces = view_spaces()
assert spaces, "factory startup retained no testable VIEW_3D RNA space"
proofs = []
for label, preview, source_textures in (
    ("source_vector", False, True), ("source_opaque_image", False, True),
    ("source_opaque_raster", False, True), ("source_alpha", True, True),
    ("optional_palette_vector", False, False), ("optional_palette_raster", True, False),
):
    space = spaces[0]
    space.shading.light = "STUDIO"
    engine._configure_sheet_view_shading(space, preview, source_textures)
    expected = ("MATERIAL", "STUDIO", "TEXTURE") if preview else (
        "SOLID", "FLAT", "TEXTURE" if source_textures else "MATERIAL")
    actual = tuple(getattr(space.shading, name) for name in ("type", "light", "color_type"))
    assert actual == expected, (label, actual, expected)
    proofs.append({"case": label, "state": list(actual)})
assert scene_color() == colors_before
for space in spaces:
    engine._configure_sheet_view_shading(space, False, True)
before = {name: image_state(obj) for name, obj in objects.items()}
saved = output / "fictional-source-texture-view.blend"
bpy.ops.wm.save_as_mainfile(filepath=str(saved), check_existing=False)
bpy.ops.wm.open_mainfile(filepath=str(saved))
after = {name: image_state(bpy.data.objects[name]) for name in objects}
assert before == after
assert scene_color() == colors_before
assert all((space.shading.type, space.shading.light, space.shading.color_type) ==
           ("SOLID", "FLAT", "TEXTURE") for space in view_spaces())
assert tuple(bpy.data.materials["Fictional red material"].diffuse_color) == (1, 0, 0, 1)
record = {"schema": "bcs.blender.source-sheet-view-native-api/1", "passed": True,
          "host": bpy.app.version_string, "background": bpy.app.background,
          "source_module_sha256": digest(Path(engine.__file__)),
          "input_png_sha256": {str(path): digest(path) for path in (rgb_path, rgba_path)},
          "view_cases": proofs, "scene_color_unchanged": colors_before,
          "image_states_before": before, "image_states_after": after,
          "saved_blend_sha256": digest(saved), "view_state_saved_reopen_preserved": True,
          "active_image_node_uv_and_packed_bytes_preserved": True,
          "desktop_import_or_visual_acceptance": False,
          "scope": "Direct source view helper and source image builder in real background bpy; synthetic controls, no desktop/extraction/full-PDF claim."}
with (output / "NATIVE-API-RESULT.json").open("x", encoding="utf-8") as stream:
    json.dump(record, stream, indent=2)
    stream.write("\n")
print(json.dumps({"host": record["host"], "passed": True, "output": str(output)}))
