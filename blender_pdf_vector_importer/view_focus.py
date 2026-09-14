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


def focus_view_on_collection(root_collection, keep_selected: bool = False) -> bool:
    """
    Select imported objects and frame them in all visible VIEW_3D areas.
    Returns True when at least one 3D view was focused.
    """
    from pdf_vector_importer.bl_import_engine import _focus_view_on_import

    if root_collection is None:
        return False
    _unhide_collection_tree(root_collection)
    return _focus_view_on_import(root_collection, keep_selected=keep_selected)
