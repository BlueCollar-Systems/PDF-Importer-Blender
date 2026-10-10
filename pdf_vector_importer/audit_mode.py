# -*- coding: utf-8 -*-
# audit_mode.py — Optional heavy self-checks ("Audit import")
# Copyright (c) 2024-2026 BlueCollar Systems — BUILT. NOT BOUGHT.
# License: MIT
"""Whether this import re-proves its own work in full.

Everyday imports run the checks that decide what each item becomes (the
step-down gates) and cheap identity checks. The exact re-proofs of every
glyph outline (Fraction arithmetic, three passes per glyph) run only in
audit mode: env BC_PDF_AUDIT=1, the import config key ``audit_import``, or
the add-on preference "Audit import (slow self-checks)". Default off.

Pure Python: the engine activates the resolved value for one import, and the
builders deep in the text pipeline read it without threading a parameter
through every layer.
"""
from __future__ import annotations

import os
from typing import Any, List, Optional

ENV_VAR = "BC_PDF_AUDIT"
CONFIG_KEY = "audit_import"
PREFERENCE = "audit_import"
_FALSE = {"0", "false", "off", "no"}

_active: List[bool] = []


def env_audit() -> Optional[bool]:
    """BC_PDF_AUDIT as a bool, or None when it is not set."""
    raw = os.environ.get(ENV_VAR, "").strip().lower()
    if not raw:
        return None
    return raw not in _FALSE


def resolve_audit_mode(config: Optional[dict] = None, preferences: Any = None) -> bool:
    """Config key, then BC_PDF_AUDIT, then the add-on preference; default off."""
    config = config if isinstance(config, dict) else {}
    if config.get(CONFIG_KEY) is not None:
        return bool(config[CONFIG_KEY])
    from_env = env_audit()
    if from_env is not None:
        return from_env
    return bool(getattr(preferences, PREFERENCE, False))


def activate(enabled: bool) -> int:
    """Make ``enabled`` the audit mode until the matching deactivate()."""
    _active.append(bool(enabled))
    return len(_active)


def deactivate(token: int) -> None:
    del _active[max(0, token - 1):]


def audit_enabled() -> bool:
    """The active import's audit mode; outside an import, BC_PDF_AUDIT (default off)."""
    if _active:
        return _active[-1]
    return bool(env_audit())
