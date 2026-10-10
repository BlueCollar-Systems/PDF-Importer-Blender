"""Native Blender smoke: every declared UI property must reach RNA.

Run with blender --background --factory-startup --python-exit-code 1
--python scripts/smoke_registered_properties.py. No PDF, pip, or network needed.
"""
from pathlib import Path
import sys

REPO = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, REPO)

import bpy
from pdf_vector_importer import operators, preferences


classes = (
    preferences.PDFVEC_OT_install_pymupdf,
    preferences.PDFVectorImporterPreferences,
    operators.IMPORT_OT_pdf_vector_cancel,
    operators.IMPORT_OT_pdf_vector,
)
# Drag-and-drop import: Blender 4.1+ only. Older versions must still load.
HAS_FILE_HANDLER = hasattr(bpy.types, "FileHandler")
assert (operators.PDFVEC_FH_import is not None) == HAS_FILE_HANDLER
if HAS_FILE_HANDLER:
    classes += (operators.PDFVEC_FH_import,)
registered = []
try:
    for cls in classes:
        bpy.utils.register_class(cls)
        registered.append(cls)
        declared = set(cls.__dict__.get("__annotations__", {}))
        if issubclass(cls, bpy.types.Operator):
            namespace, name = cls.bl_idname.split(".", 1)
            rna = getattr(getattr(bpy.ops, namespace), name).get_rna_type()
        else:
            rna = cls.bl_rna
        missing = declared - set(rna.properties.keys())
        assert not missing, f"{cls.__name__} lost UI properties: {sorted(missing)}"
    properties = bpy.ops.import_scene.pdf_vector.get_rna_type().properties
    prefs = preferences.PDFVectorImporterPreferences.bl_rna.properties
    assert "filepath" in properties, "ImportHelper's file selection must stay registered"
    assert properties["visual_style"].default == "source"
    assert prefs["default_visual_style"].default == "source"
    assert properties["pages"].default == "all"
    assert properties["text_mode"].default == "3d_text"
    assert len(properties["visual_style"].enum_items) == 3
    assert bpy.ops.pdfvec.install_pymupdf.get_rna_type().properties["confirm_network_install"].default is False
    if HAS_FILE_HANDLER:
        handler = bpy.types.FileHandler.bl_rna_get_subclass_py("PDFVEC_FH_import")
        assert handler is operators.PDFVEC_FH_import, "PDF drag-and-drop handler did not register"
        assert handler.bl_file_extensions == ".pdf"
        assert handler.bl_import_operator == "import_scene.pdf_vector"
        print("FILE_HANDLER PDFVEC_FH_import", handler.bl_file_extensions)
    else:
        print("FILE_HANDLER none (drag-and-drop needs Blender 4.1+)")
    print("NATIVE_PROPERTIES_PASS", bpy.app.version_string)
finally:
    for cls in reversed(registered):
        bpy.utils.unregister_class(cls)
