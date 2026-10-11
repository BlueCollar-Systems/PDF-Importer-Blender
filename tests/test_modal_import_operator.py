"""The main-thread modal driver dispatches controls and drains safe cleanup."""
import importlib
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest


@pytest.fixture
def modal_operator(monkeypatch):
    bpy = ModuleType("bpy")
    bpy.types = SimpleNamespace(Operator=type("Operator", (), {}))
    props = ModuleType("bpy.props")
    for name in ("BoolProperty", "EnumProperty", "FloatProperty", "StringProperty"):
        setattr(props, name, lambda **_kwargs: None)
    extras = ModuleType("bpy_extras")
    io_utils = ModuleType("bpy_extras.io_utils")
    io_utils.ImportHelper = type("ImportHelper", (), {})
    for name, module in (("bpy", bpy), ("bpy.props", props),
                         ("bpy_extras", extras), ("bpy_extras.io_utils", io_utils)):
        monkeypatch.setitem(sys.modules, name, module)
    sys.modules.pop("pdf_vector_importer.operators", None)
    operators = importlib.import_module("pdf_vector_importer.operators")
    operator = operators.IMPORT_OT_pdf_vector()
    operator.report = Mock()
    operator._set_status = Mock()
    operator._timer = object()
    context = SimpleNamespace(window_manager=SimpleNamespace(event_timer_remove=Mock()))
    operators._begin_import_session()
    try:
        yield operator, operators, context
    finally:
        operators._end_import_session()


def test_escape_is_processed_before_another_timer_chunk(modal_operator):
    operator, operators, context = modal_operator
    advanced = []

    def steps():
        advanced.append("work")
        yield
        assert operators._cancel_requested()
        return {"cancelled": True}

    operator._steps = steps()
    operator._report_result = Mock(return_value={"CANCELLED"})
    timer = operator._timer
    assert operator.modal(context, SimpleNamespace(type="ESC")) == {"RUNNING_MODAL"}
    assert advanced == []
    assert operator.modal(context, SimpleNamespace(type="TIMER", timer=timer)) == {"RUNNING_MODAL"}
    assert operator.modal(context, SimpleNamespace(type="TIMER", timer=timer)) == {"CANCELLED"}
    context.window_manager.event_timer_remove.assert_called_once_with(timer)
    assert not operators._IMPORT_ACTIVE


def test_other_timer_and_mouse_events_do_not_advance_native_work(modal_operator):
    operator, _operators, context = modal_operator
    operator._steps = MagicMock()
    assert operator.modal(context, SimpleNamespace(type="MOUSEMOVE")) == {"PASS_THROUGH"}
    assert operator.modal(context, SimpleNamespace(type="TIMER", timer=object())) == {"PASS_THROUGH"}
    operator._steps.__next__.assert_not_called()


def test_host_forced_cancel_drains_rollback_before_closing_generator(modal_operator):
    operator, operators, context = modal_operator
    events = []

    def steps():
        try:
            yield
            assert operators._cancel_requested()
            events.append("rollback-and-checkpoint")
            yield
            return {"cancelled": True}
        finally:
            events.append("release-native-resources")

    operator._steps = steps()
    next(operator._steps)
    operator.cancel(context)
    assert events == ["rollback-and-checkpoint", "release-native-resources"]
    assert not operators._IMPORT_ACTIVE


def test_failed_phase_releases_timer_and_restores_import_availability(modal_operator):
    operator, operators, context = modal_operator

    def steps():
        yield
        raise ValueError("source page is invalid")

    operator._steps = steps()
    timer = operator._timer
    assert operator.modal(context, SimpleNamespace(type="TIMER", timer=timer)) == {"RUNNING_MODAL"}
    assert operator.modal(context, SimpleNamespace(type="TIMER", timer=timer)) == {"CANCELLED"}
    context.window_manager.event_timer_remove.assert_called_once_with(timer)
    assert operators.IMPORT_OT_pdf_vector.poll(context)
    operator.report.assert_called_once_with({"ERROR"}, "PDF import failed: source page is invalid")


def test_host_released_timer_does_not_leave_import_locked(modal_operator):
    operator, operators, context = modal_operator
    context.window_manager.event_timer_remove.side_effect = ReferenceError("host window closed")

    def steps():
        yield

    operator._steps = steps()
    operator._release_import(context)
    assert not operators._IMPORT_ACTIVE
    assert operator._steps is None and operator._timer is None
