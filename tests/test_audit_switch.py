"""The heavy outline self-proofs run only in audit mode (default off).

Audit mode: BC_PDF_AUDIT=1, config["audit_import"], or the add-on preference
"Audit import (slow self-checks)". Off, each glyph keeps its native
qualification (the step-down gate) and cheap identity checks; on, behaviour is
the full exact proof as before. No Blender process is launched.
"""
import json
import sys
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from test_bl_source_outline_builder import Object, Registry, Splines, material, record
from pdf_vector_importer import audit_mode
from pdf_vector_importer import bl_source_outline_builder as b

if "bmesh" not in sys.modules:
    sys.modules["bmesh"] = NS()


@pytest.fixture
def host(monkeypatch):
    """The fake Blender of test_bl_source_outline_builder.py, with a cold proof cache."""
    b._clear_qualification_cache()
    data = NS(curves=Registry(lambda name, kind: NS(name=name, splines=Splines(), materials=[])),
              objects=Registry(Object), materials=Registry(material))
    fake = NS(data=data, context=NS(view_layer=NS(update=lambda: None)))
    monkeypatch.setitem(sys.modules, "bpy", fake)
    # Straight synthetic contours do not invoke Blender's cubic evaluator.
    monkeypatch.setattr(b, "_native_polygons",
                        lambda rings: [[tuple(map(float, p[0])) for p in ring] for ring in rings])
    yield fake
    b._clear_qualification_cache()


def _raise(*_args, **_kwargs):
    raise AssertionError("exact ink proof must not run with audit off")


def _build(representation="glyphs"):
    return b.build_source_outlines(record(), NS(objects=NS(link=lambda obj: None)),
                                   representation=representation, requested="3d_text")


@pytest.fixture
def audit_off(monkeypatch):
    monkeypatch.delenv("BC_PDF_AUDIT", raising=False)


@pytest.mark.parametrize("representation", ["glyphs", "geometry"])
def test_audit_off_build_never_runs_the_exact_ink_proof(host, monkeypatch, audit_off, representation):
    monkeypatch.setattr(b, "verify_mesh_ink", _raise)
    monkeypatch.setattr(b, "_mesh_readback", _raise)
    monkeypatch.setattr(b, "_polygon_qualification", _raise)
    qualified = []
    real_qualify = b.qualify_contours
    monkeypatch.setattr(b, "qualify_contours", lambda *a, **k: qualified.append(k.get("native", False))
                        or real_qualify(*a, **k))
    outcome = _build(representation)
    assert outcome.status == "delivered", outcome.evidence
    assert qualified == [True]  # the native step-down gate only
    proof = outcome.evidence["placements"][0]
    assert proof["proof_level"] == "light" and proof["verified"] is True
    saved = json.loads(outcome.entity["pdf_source_outline_record"])
    assert proof["initial_mesh_sha256"] == saved["initial_mesh_sha256"]
    assert len(saved["initial_mesh_sha256"]) == 64


def test_audit_on_build_runs_every_exact_proof(host, monkeypatch):
    monkeypatch.setenv("BC_PDF_AUDIT", "1")
    calls = []
    real_readback = b._mesh_readback
    monkeypatch.setattr(b, "_mesh_readback", lambda *a: calls.append("readback") or real_readback(*a))
    outcome = _build()
    assert outcome.status == "delivered", outcome.evidence
    assert calls == ["readback", "readback"]  # build readback + immediate re-proof
    assert outcome.evidence["placements"][0]["proof_level"] == "full"
    monkeypatch.setattr(b, "verify_mesh_ink", _raise)
    failed = _build()
    assert failed.status == "failed" and "audit off" in failed.evidence["detail"]


def test_light_fingerprint_binds_to_the_full_proof(host, monkeypatch, audit_off):
    outcome = _build()
    # A later audit-mode re-proof of an everyday import still matches its fingerprint.
    assert b.verify_source_outline_entity(outcome.entity)["proof_level"] == "full"


@pytest.mark.parametrize("damage", ["identity", "transform", "modifier", "material", "record"])
def test_light_check_still_rejects_identity_placement_and_material_changes(host, monkeypatch,
                                                                          audit_off, damage):
    outcome = _build()
    obj = outcome.entity
    monkeypatch.setattr(b, "_mesh_readback", _raise)
    assert b.verify_source_outline_entity(obj, light=True)["verified"]
    if damage == "identity":
        obj["pdf_text_source"] = "B"
    elif damage == "transform":
        obj.location = (0., 0.5, 0.)
    elif damage == "modifier":
        obj.modifiers.append(NS(type="NODES"))
    elif damage == "material":
        obj.data.materials[0].diffuse_color = (0., 0., 1., 1.)
    else:
        saved = json.loads(obj["pdf_source_outline_record"])
        owner = host.data.objects.get(saved["source_record_owner"])
        source = json.loads(owner["pdf_source_outline_source_record"])
        source["source_text"] = "B"
        owner["pdf_source_outline_source_record"] = json.dumps(source)
    with pytest.raises(b.OutlineVerificationError):
        b.verify_source_outline_entity(obj, light=True)


def _stacked_recheck(monkeypatch):
    from test_source_outline_delivery_blender import engine, native

    matrix = [[1., 0., 0., 0.], [0., 1., 0., 0.], [0., 0., 1., 0.], [0., 0., 0., 1.]]
    obj = NS(name="outline", type="CURVE", location=(0., -0.5, 0.), get=lambda *_a: "")
    monkeypatch.setattr(engine, "bpy", NS(data=NS(objects=NS(get=lambda _name: obj)),
                                          context=NS(view_layer=NS(update=lambda: None))))
    evidence = {"outline_source": "source_renderer_svg", "placements": [{
        "entity_id": "outline", "creation_world_matrix": matrix,
        "source_outline_sha256": "a" * 64, "source_placement_index": 0}]}
    rec = {"item_id": "page:2:text:1", "page": 2, "status": "delivered",
           "final_representation": "glyphs", "entity_ids": ["outline"],
           "attempts": [{"status": "delivered", "evidence": evidence}]}
    calls = []

    def verify(_obj, **kwargs):
        calls.append(kwargs)
        return {"verified": True, "source_outline_sha256": "a" * 64, "source_placement_index": 0}

    monkeypatch.setattr(native, "verify_source_outline_entity", verify)
    failures = engine._reverify_text_delivery_after_stack([deepcopy(rec)], page_number=2,
                                                          stack_offset_m=-0.5)
    return failures, calls


def test_stacked_recheck_is_light_with_audit_off(monkeypatch, audit_off):
    failures, calls = _stacked_recheck(monkeypatch)
    assert failures == [] and [call["light"] for call in calls] == [True]


def test_stacked_recheck_is_full_with_audit_on(monkeypatch):
    monkeypatch.setenv("BC_PDF_AUDIT", "1")
    failures, calls = _stacked_recheck(monkeypatch)
    assert failures == [] and [call["light"] for call in calls] == [False]


def test_audit_mode_precedence_and_scope(monkeypatch):
    prefs_on = NS(audit_import=True)
    monkeypatch.delenv("BC_PDF_AUDIT", raising=False)
    assert audit_mode.resolve_audit_mode({}, None) is False
    assert audit_mode.resolve_audit_mode({}, prefs_on) is True
    assert audit_mode.resolve_audit_mode({"audit_import": False}, prefs_on) is False
    monkeypatch.setenv("BC_PDF_AUDIT", "0")
    assert audit_mode.resolve_audit_mode({}, prefs_on) is False
    monkeypatch.setenv("BC_PDF_AUDIT", "1")
    assert audit_mode.resolve_audit_mode({}, None) is True
    assert audit_mode.resolve_audit_mode({"audit_import": True}, None) is True
    token = audit_mode.activate(False)
    try:
        assert audit_mode.audit_enabled() is False  # the active import wins over the env
    finally:
        audit_mode.deactivate(token)
    assert audit_mode.audit_enabled() is True


@pytest.mark.parametrize("audit", [True, False])
def test_report_records_audit_mode(monkeypatch, tmp_path, audit):
    from pdf_vector_importer import bl_import_engine as engine

    report = tmp_path / "report.json"
    engine.write_import_report("D042.pdf", {"import_text": False}, {"audit_mode": audit},
                               import_mode="vector", output_path=str(report))
    assert json.loads(report.read_text())["extra"]["audit_mode"] is audit
