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
    print("NATIVE_PROPERTIES_PASS", bpy.app.version_string)
finally:
    for cls in reversed(registered):
        bpy.utils.unregister_class(cls)
