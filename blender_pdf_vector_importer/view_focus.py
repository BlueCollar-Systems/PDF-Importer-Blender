"""Frame Blender 3D views on imported PDF collections."""
from __future__ import annotations


def _iter_collection_tree(root_collection):
    stack = [root_collection]
    seen = set()
    while stack:
        col = stack.pop()
        if col is None:
            continue
        key = id(col)
        if key in seen:
            continue
        seen.add(key)
        yield col
        try:
            stack.extend(list(col.children))
        except Exception:
            pass


def _find_layer_collection(layer_col, target_collection):
    if layer_col is None:
        return None
    try:
        if layer_col.collection == target_collection:
            return layer_col
    except Exception:
        pass
    for child in getattr(layer_col, "children", []):
        found = _find_layer_collection(child, target_collection)
        if found is not None:
            return found
    return None


def _unhide_collection_tree(root_collection) -> None:
    import bpy

    scene = bpy.context.scene
    for col in _iter_collection_tree(root_collection):
        try:
            col.hide_viewport = False
        except Exception:
            pass
        try:
            col.hide_render = False
        except Exception:
            pass

        for view_layer in scene.view_layers:
            try:
                layer_col = _find_layer_collection(view_layer.layer_collection, col)
                if layer_col is None:
                    continue
                layer_col.exclude = False
                layer_col.hide_viewport = False
                layer_col.holdout = False
                layer_col.indirect_only = False
            except Exception:
                continue


def _world_bounds_for_objects(objects):
    try:
        from mathutils import Vector
    except Exception:
        return None, None

    min_v = None
    max_v = None
    for obj in objects:
        if obj is None:
            continue
        if getattr(obj, "type", "") in {"CAMERA", "LIGHT"}:
            continue
        try:
            corners = obj.bound_box
            mw = obj.matrix_world
        except Exception:
            continue
        if not corners:
            continue
        try:
            world_pts = [mw @ Vector((c[0], c[1], c[2])) for c in corners]
        except Exception:
            continue
        for p in world_pts:
            if min_v is None:
                min_v = Vector((p.x, p.y, p.z))
                max_v = Vector((p.x, p.y, p.z))
            else:
                min_v.x = min(min_v.x, p.x)
                min_v.y = min(min_v.y, p.y)
                min_v.z = min(min_v.z, p.z)
                max_v.x = max(max_v.x, p.x)
                max_v.y = max(max_v.y, p.y)
                max_v.z = max(max_v.z, p.z)
    return min_v, max_v


def focus_view_on_collection(root_collection, keep_selected: bool = False) -> bool:
    """
    Select imported objects and frame them in all visible VIEW_3D areas.
    Returns True when at least one 3D view was focused.
    """
    import bpy

    if root_collection is None:
        return False

    _unhide_collection_tree(root_collection)

    objects = []
    try:
        objects = [obj for obj in root_collection.all_objects if obj is not None]
    except Exception:
        stack = [root_collection]
        seen_cols = set()
        while stack:
            col = stack.pop()
            if col is None or id(col) in seen_cols:
                continue
            seen_cols.add(id(col))
            try:
                objects.extend([obj for obj in col.objects if obj is not None])
            except Exception:
                pass
            try:
                stack.extend(list(col.children))
            except Exception:
                pass

    if not objects:
        return False

    visible_objects = []
    for obj in objects:
        try:
            obj.hide_set(False)
        except Exception:
            pass
        try:
            obj.hide_viewport = False
        except Exception:
            pass
        try:
            obj.hide_render = False
        except Exception:
            pass
        visible_objects.append(obj)

    min_v, max_v = _world_bounds_for_objects(visible_objects)
    view_layer = bpy.context.view_layer

    try:
        for obj in view_layer.objects:
            try:
                obj.select_set(False)
            except Exception:
                pass
    except Exception:
        pass

    selected = []
    for obj in visible_objects:
        try:
            obj.select_set(True)
            selected.append(obj)
        except Exception:
            continue

    if not selected:
        return False

    try:
        view_layer.objects.active = selected[0]
    except Exception:
        pass

    focused = False
    wm = bpy.context.window_manager
    for window in wm.windows:
        screen = window.screen
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            if region is None:
                continue
            try:
                with bpy.context.temp_override(
                    window=window,
                    screen=screen,
                    area=area,
                    region=region,
                    scene=bpy.context.scene,
                    view_layer=view_layer,
                ):
                    try:
                        space = area.spaces.active
                        if hasattr(space, "use_local_collections"):
                            space.use_local_collections = False
                        if getattr(space, "local_view", None) is not None:
                            bpy.ops.view3d.localview(frame_selected=False)
                        if min_v is not None and max_v is not None:
                            span_x = abs(max_v.x - min_v.x)
                            span_y = abs(max_v.y - min_v.y)
                            span_z = abs(max_v.z - min_v.z)
                            radius = max(span_x, span_y, span_z, 0.25)
                            space.clip_start = max(1.0e-5, min(float(space.clip_start), radius / 10000.0))
                            space.clip_end = max(float(space.clip_end), radius * 200.0, 1000.0)
                    except Exception:
                        pass

                    try:
                        bpy.ops.view3d.view_axis(type="TOP", align_active=False)
                    except Exception:
                        pass
                    try:
                        rv3d = area.spaces.active.region_3d
                        if rv3d is not None:
                            rv3d.view_perspective = "ORTHO"
                            if min_v is not None and max_v is not None:
                                center = (min_v + max_v) * 0.5
                                span_x = abs(max_v.x - min_v.x)
                                span_y = abs(max_v.y - min_v.y)
                                span_z = abs(max_v.z - min_v.z)
                                radius = max(span_x, span_y, span_z, 0.25)
                                rv3d.view_location = center
                                rv3d.view_distance = max(radius * 1.35, 0.4)
                    except Exception:
                        pass
                    try:
                        bpy.ops.view3d.view_selected(use_all_regions=False)
                    except Exception:
                        pass
                focused = True
            except Exception:
                continue

    if not focused:
        for window in wm.windows:
            screen = window.screen
            for area in screen.areas:
                if area.type != "VIEW_3D":
                    continue
                region = next((r for r in area.regions if r.type == "WINDOW"), None)
                if region is None:
                    continue
                try:
                    with bpy.context.temp_override(
                        window=window,
                        screen=screen,
                        area=area,
                        region=region,
                        scene=bpy.context.scene,
                        view_layer=view_layer,
                    ):
                        try:
                            space = area.spaces.active
                            if hasattr(space, "use_local_collections"):
                                space.use_local_collections = False
                            if getattr(space, "local_view", None) is not None:
                                bpy.ops.view3d.localview(frame_selected=False)
                        except Exception:
                            pass
                        bpy.ops.view3d.view_axis(type="TOP", align_active=False)
                        try:
                            rv3d = area.spaces.active.region_3d
                            if rv3d is not None:
                                rv3d.view_perspective = "ORTHO"
                                if min_v is not None and max_v is not None:
                                    center = (min_v + max_v) * 0.5
                                    span_x = abs(max_v.x - min_v.x)
                                    span_y = abs(max_v.y - min_v.y)
                                    span_z = abs(max_v.z - min_v.z)
                                    radius = max(span_x, span_y, span_z, 0.25)
                                    rv3d.view_location = center
                                    rv3d.view_distance = max(radius * 1.35, 0.4)
                        except Exception:
                            pass
                        try:
                            bpy.ops.view3d.view_all(center=False)
                        except Exception:
                            pass
                    focused = True
                except Exception:
                    continue

    if not keep_selected:
        for obj in objects:
            try:
                obj.select_set(False)
            except Exception:
                pass

    return focused
