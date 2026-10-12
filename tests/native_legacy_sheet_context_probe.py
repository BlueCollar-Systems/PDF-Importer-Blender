"""Opt-in real 3.1 context construction only; no operators or foreground shim.

Run Blender with --background --factory-startup --python this_file -- SOURCE OUT.
OUT must not exist. This never certifies desktop rendering or full autofocus.
"""
from pathlib import Path
import hashlib
import json
import sys

import bpy

source, output = map(Path, sys.argv[sys.argv.index("--") + 1:])
assert not output.exists()
assert bpy.app.background is True
assert bpy.app.version[:2] == (3, 1), "This bounded legacy probe is for Blender 3.1 only"
sys.path.insert(0, str(source))
from pdf_vector_importer import bl_import_engine as engine
assert Path(engine.__file__).resolve().is_relative_to(source.resolve())
assert not callable(getattr(bpy.context, "temp_override", None))


def identity(value):
    return value.as_pointer() if hasattr(value, "as_pointer") else id(value)


before = {key: identity(getattr(bpy.context, key)) for key in (
    "window", "screen", "area", "region", "space_data", "region_data", "scene", "view_layer")}
views = []
for window in bpy.context.window_manager.windows:
    for area in window.screen.areas:
        if area.type != "VIEW_3D":
            continue
        region = next((candidate for candidate in area.regions if candidate.type == "WINDOW"), None)
        if region is None:
            continue
        with engine._sheet_view_context(window, window.screen, area, region,
                                        bpy.context.view_layer) as override:
            assert type(override) is dict
            expected = dict(window=window, screen=window.screen, area=area, region=region,
                            scene=bpy.context.scene, view_layer=bpy.context.view_layer,
                            space_data=area.spaces.active, region_data=area.spaces.active.region_3d)
            assert all(identity(override[key]) == identity(value) for key, value in expected.items())
            views.append({"area_type": area.type, "region_type": region.type,
                          "space_type": override["space_data"].type,
                          "has_native_region_data": override["region_data"] is not None,
                          "context_members": sorted(expected)})
assert views, "No factory VIEW_3D context available"
after = {key: identity(getattr(bpy.context, key)) for key in before}
assert before == after
assert bpy.app.background is True
result = dict(schema="bcs.blender-legacy-sheet-context-api/1", host_version=list(bpy.app.version),
              background_unchanged=True, context_unchanged=True, operators_called=False,
              desktop_acceptance=False, full_import_acceptance=False, view_contexts=views,
              engine_sha256=hashlib.sha256(Path(engine.__file__).read_bytes()).hexdigest(),
              probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print("LEGACY_CONTEXT_API_PASS")
