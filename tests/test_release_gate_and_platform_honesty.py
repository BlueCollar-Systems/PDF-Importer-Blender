"""Two protections the independent review found missing.

1. ``_release_identity.json`` is the SINGLE discriminator for the production pip
   gate. ``preferences._is_packaged_release()`` keys on it, and it controls operator
   registration plus poll/invoke/execute. If a packaging regression dropped that one
   file, every customer install would silently look like an "unmanifested source
   tree" and the pip/network installer would re-register -- with CI green, because
   nothing checked for it.

2. The "bundled runtime could not load" error told every user to reinstall the
   release ZIP. The bundle is ``cp310-abi3-win_amd64`` and ships only .pyd/.dll
   binaries, so on macOS/Linux that is advice to repeat the one action that cannot
   possibly work. Whether to support those platforms is a product decision; telling
   the truth about what the artifact contains is not.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

REPO_DIR = Path(__file__).resolve().parents[1]
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from pdf_vector_importer import dependency_manager  # noqa: E402
from pdf_vector_importer.build_identity import IDENTITY_FILENAME  # noqa: E402
from scripts import smoke_release_zip  # noqa: E402

IDENTITY_MEMBER = "pdf_vector_importer/%s" % IDENTITY_FILENAME


class TestReleaseIdentityIsContractual(unittest.TestCase):
    def test_identity_file_is_a_required_zip_member(self) -> None:
        self.assertIn(
            IDENTITY_MEMBER,
            smoke_release_zip.REQUIRED_MEMBERS,
            "the production pip gate keys on this file; if it is not contractual, a "
            "packaging regression re-enables the installer for every customer",
        )

    def test_smoke_rejects_a_zip_missing_the_identity_file(self) -> None:
        """The protection has to actually fire, not just be listed."""
        with tempfile.TemporaryDirectory(prefix="bl_identity_gate_") as tmp:
            zip_path = Path(tmp) / "no-identity.zip"
            members = set(smoke_release_zip.REQUIRED_MEMBERS) - {IDENTITY_MEMBER}
            with zipfile.ZipFile(zip_path, "w") as archive:
                for member in sorted(members):
                    archive.writestr(member, "")
            result = subprocess.run(
                [sys.executable, str(Path(smoke_release_zip.__file__).resolve()),
                 str(zip_path)],
                capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                IDENTITY_FILENAME, result.stdout + result.stderr,
                "the failure must name the missing file so the cause is obvious",
            )

    def test_the_gate_still_keys_on_that_exact_filename(self) -> None:
        """Guards the coupling: if preferences stops using this name, the zip
        contract above is protecting the wrong file."""
        source = (REPO_DIR / "pdf_vector_importer" / "preferences.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("IDENTITY_FILENAME", source)
        self.assertIn("_is_packaged_release", source)


class TestPlatformHonesty(unittest.TestCase):
    def test_windows_keeps_the_reinstall_guidance(self) -> None:
        # Both calls must sit INSIDE the patch: bundled_runtime_platform_supported()
        # reads sys.platform at call time, so asserting it outside only passed
        # because the author happened to be on Windows. CI runs Linux and caught it.
        with patch.object(dependency_manager.sys, "platform", "win32"), patch.object(
            dependency_manager.platform, "machine", lambda: "AMD64"
        ):
            msg = dependency_manager.runtime_unavailable_message()
            self.assertTrue(dependency_manager.bundled_runtime_platform_supported())
        self.assertIn("Reinstall", msg)

    def test_windows_on_arm_is_reported_unsupported(self) -> None:
        # The bundled wheel is win_amd64. A native ARM64 Blender cannot load it, so
        # "reinstall" would be the same futile advice as on macOS.
        with patch.object(dependency_manager.sys, "platform", "win32"), patch.object(
            dependency_manager.platform, "machine", lambda: "ARM64"
        ):
            supported = dependency_manager.bundled_runtime_platform_supported()
            msg = dependency_manager.runtime_unavailable_message()
        self.assertFalse(supported)
        self.assertNotIn("Reinstall the official", msg)
        self.assertIn("Windows 64-bit (x64)", msg)
        self.assertIn("Windows on ARM", msg)
        self.assertIn("will not change that", msg)

    def test_unknown_windows_processor_keeps_the_reinstall_guidance(self) -> None:
        with patch.object(dependency_manager.sys, "platform", "win32"), patch.object(
            dependency_manager.platform, "machine", lambda: ""
        ):
            self.assertTrue(dependency_manager.bundled_runtime_platform_supported())

    def test_non_windows_does_not_advise_a_futile_reinstall(self) -> None:
        for plat in ("darwin", "linux"):
            with self.subTest(platform=plat):
                with patch.object(dependency_manager.sys, "platform", plat):
                    supported = dependency_manager.bundled_runtime_platform_supported()
                    msg = dependency_manager.runtime_unavailable_message()
                self.assertFalse(supported)
                # It may mention reinstalling -- to say it will NOT help. What it
                # must never do is *advise* it as the remedy.
                self.assertNotIn(
                    "Reinstall the official", msg,
                    "reinstalling a Windows-only bundle cannot help on %s" % plat,
                )
                self.assertIn(
                    "will not change that", msg,
                    "the message must actively disclaim the futile remedy, not just "
                    "omit it -- users arrive already assuming reinstall is the fix",
                )
                self.assertIn("Windows", msg, "the real constraint must be named")
                self.assertIn(plat, msg, "the message must name the actual platform")

    def test_message_never_claims_a_network_action(self) -> None:
        for plat in ("win32", "darwin", "linux"):
            with patch.object(dependency_manager.sys, "platform", plat):
                msg = dependency_manager.runtime_unavailable_message()
            self.assertIn("did not download", msg)

    def test_engine_uses_the_platform_aware_message(self) -> None:
        source = (REPO_DIR / "pdf_vector_importer" / "bl_import_engine.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("runtime_unavailable_message", source)
        self.assertNotIn(
            '"The bundled PyMuPDF runtime could not load. Reinstall the "', source,
            "the hardcoded platform-blind message must be gone from the engine",
        )


class _Layout:
    """Records what a Blender UI draw() call would show."""

    def __init__(self) -> None:
        self.labels: list[str] = []
        self.operators: list[str] = []

    def box(self):
        return self

    def row(self):
        return self

    def separator(self) -> None:
        pass

    def prop(self, *_args, **_kwargs) -> None:
        pass

    def label(self, text: str = "", icon: str = "") -> None:
        self.labels.append(text)

    def operator(self, idname: str, icon: str = "") -> None:
        self.operators.append(idname)


def _fresh_preferences(monkeypatch):
    import types

    bpy = types.ModuleType("bpy")
    props = types.ModuleType("bpy.props")
    for name in ("BoolProperty", "EnumProperty", "StringProperty", "FloatProperty"):
        setattr(props, name, lambda **_kwargs: None)
    bpy.props = props

    class _Base:
        pass

    bpy.types = types.SimpleNamespace(Operator=_Base, AddonPreferences=_Base)
    bpy.utils = types.SimpleNamespace(register_class=lambda c: None, unregister_class=lambda c: None)
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    monkeypatch.setitem(sys.modules, "bpy.props", props)
    monkeypatch.delitem(sys.modules, "pdf_vector_importer.preferences", raising=False)
    import importlib

    return importlib.import_module("pdf_vector_importer.preferences")


def _draw_preferences_panel(monkeypatch, plat: str, machine: str) -> _Layout:
    import types

    preferences = _fresh_preferences(monkeypatch)
    monkeypatch.setattr(preferences, "_is_packaged_release", lambda: True)
    monkeypatch.setattr(
        preferences, "runtime_diagnostics", lambda: "Python 3.11 - PyMuPDF NOT available (simulated)"
    )
    layout = _Layout()
    panel = types.SimpleNamespace(layout=layout, pymupdf_installed=False, remember_last_directory=False)
    with patch.object(dependency_manager.sys, "platform", plat), patch.object(
        dependency_manager.platform, "machine", lambda: machine
    ):
        preferences.PDFVectorImporterPreferences.draw(panel, None)
    sys.modules.pop("pdf_vector_importer.preferences", None)
    return layout


import pytest  # noqa: E402


@pytest.mark.parametrize(
    ("plat", "machine"), [("darwin", "arm64"), ("linux", "x86_64"), ("win32", "ARM64")]
)
def test_preferences_panel_tells_unsupported_systems_the_truth(monkeypatch, plat, machine):
    layout = _draw_preferences_panel(monkeypatch, plat, machine)
    text = " ".join(layout.labels)
    assert "Reinstall the official" not in text, "a reinstall cannot fix an unsupported system"
    assert "Windows 64-bit (x64)" in text
    assert "will not change that" in text
    assert layout.operators == [], "packaged releases never offer the pip installer"
    # Blender labels do not wrap: one sentence per row keeps every word visible.
    assert all(len(label) <= 140 for label in layout.labels), layout.labels


def test_preferences_panel_on_windows_x64_keeps_the_reinstall_advice(monkeypatch):
    layout = _draw_preferences_panel(monkeypatch, "win32", "AMD64")
    text = " ".join(layout.labels)
    assert "Reinstall the official PDF Vector Importer release ZIP." in text
    assert "did not download packages or run pip" in text


def test_add_on_list_warns_windows_only_and_register_uses_the_honest_message():
    from pdf_vector_importer import bl_info

    assert bl_info.get("warning") == "Windows 64-bit only"
    source = (REPO_DIR / "pdf_vector_importer" / "__init__.py").read_text(encoding="utf-8")
    assert "runtime_unavailable_message()" in source
    assert "Reinstall the official release ZIP; runtime checks" not in source


def test_docs_say_windows_64_bit_only_and_agree_on_versions():
    readme = (REPO_DIR / "README.md").read_text(encoding="utf-8")
    compat = (REPO_DIR / "COMPATIBILITY.md").read_text(encoding="utf-8")
    for doc in (readme, compat):
        assert "Windows 64-bit (x64) only" in doc
        assert "macOS" in doc and "Linux" in doc and "Windows on ARM" in doc
    # No install paths for systems the ZIP cannot run on.
    assert "Library/Application Support" not in readme
    assert ".config/blender" not in readme
    # README must not keep a softer claim than COMPATIBILITY.md for old Blender.
    assert "Expected only after legacy branch testing" not in readme
    assert "2.83" not in readme or "Not supported" in readme
    for doc in (readme, compat):
        assert "Glyphs" in doc and "3.1.2" in doc


if __name__ == "__main__":
    unittest.main(verbosity=2)
