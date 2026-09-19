"""Optional white display backing; never source PDF paint or drawing geometry."""
from __future__ import annotations

import math


def background_spec(width_mm, height_mm, source_bottoms, *, enabled=True, style="source"):
    if not enabled or str(style).strip().lower() != "source":
        return None
    width, height = float(width_mm), float(height_mm)
    if not all(math.isfinite(value) and value > 0 for value in (width, height)):
        raise ValueError("Invalid PDF page dimensions for display background")
    bottoms = tuple(float(value) for value in source_bottoms)
    if not all(math.isfinite(value) for value in bottoms):
        raise ValueError("Nonfinite source display depth")
    return {"points_mm": [(0., 0.), (width, 0.), (width, height), (0., height)],
            "depth_m": min((0.,) + bottoms) - 0.00005,
            "role": "display_only_page_background", "source_item": False}


def add_page_background(collection, width_mm, height_mm, *, enabled=True, style="source"):
    """Build one separately hideable backing beneath all current native paint."""
    if not enabled or str(style).strip().lower() != "source":
        return None
    import bpy
    from .bl_geometry_builder import _create_face_mesh, _get_or_create_material
    from .image_paint_order import _world_corners

    bpy.context.view_layer.update()
    graph = bpy.context.evaluated_depsgraph_get()
    bottoms = []
    for obj in collection.all_objects:
        if obj.type in {"FONT", "CURVE", "MESH"}:
            corners = _world_corners(obj, graph)
            if corners:
                bottoms.append(min(point.z for point in corners))
    spec = background_spec(width_mm, height_mm, bottoms, enabled=enabled, style=style)
    material = _get_or_create_material((1., 1., 1.), {}, style="source")
    obj = _create_face_mesh("PDF White Page Background (Display Aid)", spec["points_mm"],
                            collection, material, z_offset_m=spec["depth_m"])
    obj["pdf_display_aid"] = spec["role"]
    obj["pdf_display_aid_description"] = "White page background; hide this object to see the scene background. Not source PDF paint."
    obj["pdf_page_width_mm"] = float(width_mm)
    obj["pdf_page_height_mm"] = float(height_mm)
    obj.hide_select = True
    bpy.context.view_layer.update()
    corners = _world_corners(obj, graph)
    if not corners or max(point.z for point in corners) >= min((0.,) + tuple(bottoms)):
        raise ValueError("White display background did not remain behind source paint")
    return {"object": obj.name, "role": spec["role"], "width_mm": float(width_mm),
            "height_mm": float(height_mm), "display_depth_m": spec["depth_m"],
            "source_item": False, "individually_hideable": True}
