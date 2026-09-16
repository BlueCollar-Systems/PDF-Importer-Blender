"""Final-state re-verification resolves entity names from one registry snapshot.

``bpy.data.objects.get`` walks the ID list per call; the snapshot answers the same
question in one pass and is rebuilt after a cleanup removes objects.  Registries
that cannot be iterated (host-test doubles) keep answering through ``get``.
"""

from __future__ import annotations

from pathlib import Path
import sys
import types

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

if "bpy" not in sys.modules:
    sys.modules["bpy"] = types.SimpleNamespace()
if not hasattr(sys.modules["bpy"], "app"):
    sys.modules["bpy"].app = types.SimpleNamespace(version=(4, 1, 0))
if not hasattr(sys.modules["bpy"], "types"):
    sys.modules["bpy"].types = types.SimpleNamespace(Collection=object, Object=object)
if "bmesh" not in sys.modules:
    sys.modules["bmesh"] = types.SimpleNamespace()

from pdf_vector_importer import bl_import_engine  # noqa: E402


class _Registry(list):
    """Iterable like bpy.data.objects, with a get() that must stay unused."""

    def __init__(self, objects):
        super().__init__(objects)
        self.get_calls = 0
        self.iterations = 0

    def __iter__(self):
        self.iterations += 1
        return super().__iter__()

    def get(self, name):
        self.get_calls += 1
        return next((obj for obj in list.__iter__(self) if obj.name == name), None)


def _obj(name, kind="FONT"):
    return types.SimpleNamespace(name=name, type=kind, location=[0.01, 0.02, 0.0])


def test_snapshot_answers_every_name_in_one_pass():
    registry = _Registry([_obj("Text_1"), _obj("Text_2"), _obj("Mesh_3", "MESH")])
    lookup = bl_import_engine._ObjectNameLookup(registry)
    assert lookup.get("Text_2").name == "Text_2"
    assert lookup.get("Mesh_3").type == "MESH"
    assert lookup.get("Missing") is None
    assert registry.iterations == 1
    assert registry.get_calls == 0


def test_first_object_wins_for_a_repeated_name_like_a_linear_search():
    first, second = _obj("Text_1"), _obj("Text_1", "MESH")
    lookup = bl_import_engine._ObjectNameLookup(_Registry([first, second]))
    assert lookup.get("Text_1") is first


def test_invalidate_rebuilds_after_objects_are_removed():
    registry = _Registry([_obj("Text_1"), _obj("Text_2")])
    lookup = bl_import_engine._ObjectNameLookup(registry)
    assert lookup.get("Text_2") is not None
    registry.pop()
    assert lookup.get("Text_2") is not None  # stale until told otherwise
    lookup.invalidate()
    assert lookup.get("Text_2") is None
    assert registry.iterations == 2


def test_registries_without_iteration_fall_back_to_get():
    obj = _obj("Text_9")
    calls = []

    def get(name):
        calls.append(name)
        return obj if name == obj.name else None

    lookup = bl_import_engine._ObjectNameLookup(types.SimpleNamespace(get=get))
    assert lookup.get("Text_9") is obj
    assert lookup.get("Other") is None
    assert calls == ["Text_9", "Other"]
    assert bl_import_engine._ObjectNameLookup(None).get("Text_9") is None


def test_reverification_resolves_entities_through_the_snapshot(monkeypatch):
    registry = _Registry([_obj("Text_1"), _obj("Text_2")])
    monkeypatch.setattr(
        bl_import_engine,
        "bpy",
        types.SimpleNamespace(data=types.SimpleNamespace(objects=registry)),
    )
    records = [
        {
            "page": 1,
            "status": "delivered",
            "final_representation": "text",
            "entity_ids": [name],
            "attempts": [{"status": "delivered", "evidence": {"actual_location_m": [0.01, 0.12]}}],
        }
        for name in ("Text_1", "Text_2")
    ]
    failures = bl_import_engine._reverify_text_delivery_after_stack(
        records, page_number=1, stack_offset_m=-0.10
    )
    assert failures == []
    assert all(record["final_state_verification"]["status"] == "verified" for record in records)
    assert registry.iterations == 1
    assert registry.get_calls == 0
