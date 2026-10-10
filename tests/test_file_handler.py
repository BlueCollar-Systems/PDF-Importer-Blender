"""Drag-and-drop PDF import (Blender 4.1+ FileHandler) with a stand-in for bpy.

Blender 4.1 added file handlers: dropping a file on the window runs the
importer named by a handler for that extension, with ``filepath`` already set.
These tests pin three things:
- the handler exists and registers only where Blender has FileHandler, so
  Blender 3.1-4.0 still load the add-on;
- a dropped PDF goes straight to the import options for that file (no file
  browser, no jump to the last folder);
- File > Import with no file chosen still opens the file browser as before.
"""
from __future__ import annotations

import importlib
import os
import sys
import types

import pytest

from pdf_vector_importer import dependency_manager

_FRESH = (
    "pdf_vector_importer.operators",
    "pdf_vector_importer.preferences",
)


class _Registry:
    def __init__(self) -> None:
        self.registered: list[type] = []
        self.menu: list[object] = []


def _install_bpy(monkeypatch: pytest.MonkeyPatch, *, file_handler: bool) -> _Registry:
    for name in _FRESH:
        monkeypatch.delitem(sys.modules, name, raising=False)
    registry = _Registry()

    class Operator:
        pass

    class AddonPreferences:
        pass

    class FileHandler:
        pass

    class ImportHelper:
        pass

    class ImportMenu:
        @staticmethod
        def append(callback) -> None:
            registry.menu.append(callback)

        @staticmethod
        def remove(callback) -> None:
            registry.menu.remove(callback)

    bpy_types = dict(
        AddonPreferences=AddonPreferences,
        Collection=object,
        Material=object,
        Object=object,
        Operator=Operator,
        TOPBAR_MT_file_import=ImportMenu,
        VectorFont=object,
    )
    if file_handler:
        bpy_types["FileHandler"] = FileHandler

    bpy = types.ModuleType("bpy")
    bpy.app = types.SimpleNamespace(version=(5, 2, 0) if file_handler else (4, 0, 0))
    bpy.types = types.SimpleNamespace(**bpy_types)
    bpy.utils = types.SimpleNamespace(
        register_class=lambda cls: registry.registered.append(cls),
        unregister_class=lambda cls: registry.registered.remove(cls),
    )
    bpy.ops = types.SimpleNamespace(wm=types.SimpleNamespace(redraw_timer=lambda **_k: None))

    props = types.ModuleType("bpy.props")
    for name in ("BoolProperty", "EnumProperty", "FloatProperty", "StringProperty"):
        setattr(props, name, lambda **_kwargs: None)
    io_utils = types.ModuleType("bpy_extras.io_utils")
    io_utils.ImportHelper = ImportHelper
    bpy_extras = types.ModuleType("bpy_extras")
    bpy_extras.io_utils = io_utils

    monkeypatch.setitem(sys.modules, "bpy", bpy)
    monkeypatch.setitem(sys.modules, "bpy.props", props)
    monkeypatch.setitem(sys.modules, "bmesh", types.ModuleType("bmesh"))
    monkeypatch.setitem(sys.modules, "bpy_extras", bpy_extras)
    monkeypatch.setitem(sys.modules, "bpy_extras.io_utils", io_utils)
    monkeypatch.setattr(dependency_manager, "ensure_pymupdf_runtime", lambda **_k: True)
    monkeypatch.setattr(dependency_manager, "print_diagnostics", lambda: None)
    monkeypatch.setattr(dependency_manager, "report_host_python_floor", lambda: True)
    return registry


@pytest.fixture(autouse=True)
def _drop_fresh_modules():
    yield
    for name in _FRESH:
        sys.modules.pop(name, None)


def _operators():
    return importlib.import_module("pdf_vector_importer.operators")


def test_handler_exists_and_registers_where_blender_has_file_handlers(monkeypatch):
    registry = _install_bpy(monkeypatch, file_handler=True)
    operators = _operators()
    handler = operators.PDFVEC_FH_import

    assert handler is not None
    assert issubclass(handler, sys.modules["bpy"].types.FileHandler)
    assert handler.bl_idname == "PDFVEC_FH_import"
    assert handler.bl_import_operator == operators.IMPORT_OT_pdf_vector.bl_idname == "import_scene.pdf_vector"
    assert handler.bl_file_extensions == ".pdf"

    addon = importlib.import_module("pdf_vector_importer")
    addon.register()
    assert handler in registry.registered
    # The handler names the importer, so it registers after it.
    assert registry.registered.index(handler) > registry.registered.index(operators.IMPORT_OT_pdf_vector)
    addon.unregister()
    assert handler not in registry.registered


def test_older_blender_without_file_handlers_still_loads(monkeypatch):
    registry = _install_bpy(monkeypatch, file_handler=False)
    operators = _operators()
    assert operators.PDFVEC_FH_import is None

    addon = importlib.import_module("pdf_vector_importer")
    addon.register()
    assert operators.IMPORT_OT_pdf_vector in registry.registered
    assert not any("FH" in cls.__name__ for cls in registry.registered)
    addon.unregister()
    assert registry.registered == []


@pytest.mark.parametrize(
    ("area_type", "accepted"),
    [("VIEW_3D", True), ("OUTLINER", True), ("IMAGE_EDITOR", False), ("NODE_EDITOR", False), (None, False)],
)
def test_drop_targets_are_the_3d_view_and_outliner(monkeypatch, area_type, accepted):
    _install_bpy(monkeypatch, file_handler=True)
    operators = _operators()
    area = None if area_type is None else types.SimpleNamespace(type=area_type)
    assert operators.PDFVEC_FH_import.poll_drop(types.SimpleNamespace(area=area)) is accepted


class _WindowManager:
    def __init__(self, *, old_dialog: bool = False) -> None:
        self.calls: list[tuple] = []
        self._old_dialog = old_dialog

    def fileselect_add(self, operator) -> None:
        self.calls.append(("fileselect_add", operator.filepath))

    def invoke_props_dialog(self, operator, **kwargs):
        if self._old_dialog and kwargs:
            raise TypeError("invoke_props_dialog() got an unexpected keyword argument 'title'")
        self.calls.append(("invoke_props_dialog", operator.filepath, kwargs))
        return {"RUNNING_MODAL"}


def _context(wm, last_dir: str = ""):
    prefs = types.SimpleNamespace(
        default_visual_style="source",
        remember_last_directory=True,
        last_import_dir=last_dir,
    )
    addon = types.SimpleNamespace(preferences=prefs)
    preferences = types.SimpleNamespace(addons={"pdf_vector_importer": addon})
    return types.SimpleNamespace(window_manager=wm, preferences=preferences)


def _operator(operators, filepath: str, *, is_set: bool):
    op = operators.IMPORT_OT_pdf_vector()
    op.filepath = filepath
    op.properties = types.SimpleNamespace(is_property_set=lambda name: is_set and name == "filepath")
    return op


def test_dropped_pdf_opens_its_options_not_the_file_browser(monkeypatch, tmp_path):
    _install_bpy(monkeypatch, file_handler=True)
    operators = _operators()
    pdf = tmp_path / "D042 sheet.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    other_dir = tmp_path / "last"
    other_dir.mkdir()
    wm = _WindowManager()
    op = _operator(operators, str(pdf), is_set=True)

    result = op.invoke(_context(wm, last_dir=str(other_dir)), None)

    assert result == {"RUNNING_MODAL"}
    assert op.filepath == str(pdf), "the dropped file must not be replaced by the last folder"
    assert [c[0] for c in wm.calls] == ["invoke_props_dialog"]
    kwargs = wm.calls[0][2]
    assert kwargs["title"] == "D042 sheet.pdf"
    assert kwargs["confirm_text"] == operators.IMPORT_OT_pdf_vector.bl_label
    # The user's default look still applies to a dropped file.
    assert op.visual_style == "source"


def test_dropped_pdf_on_older_dialog_api_still_opens_options(monkeypatch, tmp_path):
    _install_bpy(monkeypatch, file_handler=True)
    operators = _operators()
    pdf = tmp_path / "EX101.PDF"
    pdf.write_bytes(b"%PDF-1.4\n")
    wm = _WindowManager(old_dialog=True)
    op = _operator(operators, str(pdf), is_set=True)

    assert op.invoke(_context(wm), None) == {"RUNNING_MODAL"}
    assert wm.calls == [("invoke_props_dialog", str(pdf), {})]


def test_file_import_menu_without_a_file_opens_the_browser_as_before(monkeypatch, tmp_path):
    _install_bpy(monkeypatch, file_handler=True)
    operators = _operators()
    last = tmp_path / "jobs"
    last.mkdir()
    wm = _WindowManager()
    op = _operator(operators, "", is_set=False)

    assert op.invoke(_context(wm, last_dir=str(last)), None) == {"RUNNING_MODAL"}
    # Unchanged behaviour: the browser opens in the remembered folder.
    assert wm.calls == [("fileselect_add", os.path.join(str(last), ""))]


@pytest.mark.parametrize("name", ["drawing.dxf", "missing.pdf"])
def test_supplied_path_that_is_not_an_existing_pdf_falls_back_to_the_browser(monkeypatch, tmp_path, name):
    _install_bpy(monkeypatch, file_handler=True)
    operators = _operators()
    path = tmp_path / name
    if name.endswith(".dxf"):
        path.write_bytes(b"0\nEOF\n")
    wm = _WindowManager()
    op = _operator(operators, str(path), is_set=True)

    op.invoke(_context(wm), None)
    assert [c[0] for c in wm.calls] == ["fileselect_add"]


def test_remembered_path_from_last_run_does_not_count_as_dropped(monkeypatch, tmp_path):
    # Blender restores last-run values as "ghost" properties: is_property_set()
    # is False for them, so File > Import keeps showing the browser.
    _install_bpy(monkeypatch, file_handler=True)
    operators = _operators()
    pdf = tmp_path / "last_time.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    wm = _WindowManager()
    op = _operator(operators, str(pdf), is_set=False)

    op.invoke(_context(wm), None)
    assert [c[0] for c in wm.calls] == ["fileselect_add"]
