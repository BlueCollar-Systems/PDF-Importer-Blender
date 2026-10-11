"""Cooperative cancellation, complexity, and page-resume contracts."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import stat
from typing import Any, Dict, Iterable, Sequence


class ImportCancelledError(RuntimeError):
    """Raised at a safe work heartbeat after cancellation is requested."""


def resume_content_sha256(package_root: str | Path | None = None) -> str:
    """Bind checkpoints to actual code/runtime bytes, never file timestamps."""
    root = Path(package_root) if package_root is not None else Path(__file__).resolve().parent
    root = root.absolute()
    if root.is_symlink() or getattr(root.lstat(), "st_file_attributes", 0) & 0x400:
        raise ValueError("resume code identity cannot traverse a linked package")
    entries = []
    def on_error(error):
        raise ValueError("resume code identity cannot read the package") from error
    for directory, names, filenames in os.walk(root, onerror=on_error, followlinks=False):
        names[:] = sorted(name for name in names if name != "__pycache__")
        for name in names:
            candidate = Path(directory) / name
            if candidate.is_symlink() or getattr(candidate.lstat(), "st_file_attributes", 0) & 0x400:
                raise ValueError("resume code identity cannot traverse linked code")
        for name in sorted(filenames):
            if Path(name).suffix.lower() not in {".py", ".pyd", ".dll", ".so"}:
                continue
            candidate = Path(directory) / name
            meta = candidate.lstat()
            if not stat.S_ISREG(meta.st_mode) or stat.S_ISLNK(meta.st_mode) or getattr(meta, "st_file_attributes", 0) & 0x400:
                raise ValueError("resume code identity requires ordinary code files")
            content = hashlib.sha256()
            with candidate.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    content.update(chunk)
            entries.append((candidate.relative_to(root).as_posix(), content.hexdigest()))
    if not entries:
        raise ValueError("resume code identity has no importer code files")
    digest = hashlib.sha256()
    for name, content in sorted(entries):
        digest.update(name.encode("utf-8") + b"\0" + content.encode("ascii") + b"\0")
    return digest.hexdigest()


def cancel_heartbeat_interval(total_items: int, *, maximum_gap: int = 25) -> int:
    """Return an update interval with a hard upper bound on cancel latency."""
    total = max(1, int(total_items or 0))
    gap = max(1, int(maximum_gap or 1))
    return max(1, min(gap, total // 100 or 1))


def resume_config_sha256(config: Dict[str, Any]) -> str:
    """Bind resume state to delivery-affecting settings, not evidence paths."""
    import hashlib

    operational = {
        "resume",
        "resume_checkpoint_path",
        "import_report_path",
    }
    payload = {
        str(key): value
        for key, value in dict(config or {}).items()
        if str(key) not in operational and not callable(value)
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def estimate_import_complexity(
    *,
    page_count: int,
    primitive_count: int,
    text_item_count: int,
    glyph_count: int,
    text_mode: str,
) -> Dict[str, Any]:
    """Return a stable, representation-aware work estimate for status/reporting."""
    mode = str(text_mode or "3d_text").strip().lower()
    item_weights = {
        "labels": 1,
        "text": 2,
        "3d_text": 5,
        "glyphs": 6,
        "geometry": 8,
        "raster": 3,
    }
    glyph_weights = {
        "labels": 0.1,
        "text": 0.25,
        "3d_text": 1.0,
        "glyphs": 1.5,
        "geometry": 2.0,
        "raster": 0.5,
    }
    pages = max(0, int(page_count or 0))
    primitives = max(0, int(primitive_count or 0))
    text_items = max(0, int(text_item_count or 0))
    glyphs = max(0, int(glyph_count or 0))
    work_units = int(round(
        pages * 25
        + primitives
        + text_items * item_weights.get(mode, item_weights["3d_text"])
        + glyphs * glyph_weights.get(mode, glyph_weights["3d_text"])
    ))
    if work_units < 150:
        tier = "small"
    elif work_units < 400:
        tier = "medium"
    elif work_units < 1500:
        tier = "large"
    else:
        tier = "extreme"
    message = (
        f"{tier.title()} {mode} import estimate: {work_units} work units across "
        f"{pages} page{'s' if pages != 1 else ''}; progress updates at object "
        "heartbeats and interrupted work resumes at the next unfinished page."
    )
    return {
        "schema": "bcs.blender.import_complexity/1.0",
        "text_mode": mode,
        "pages": pages,
        "primitives": primitives,
        "text_items": text_items,
        "glyphs": glyphs,
        "work_units": work_units,
        "tier": tier,
        "cancel_granularity": "object_heartbeat",
        "resume_granularity": "completed_page",
        "message": message,
    }


def _page_selector(pages: Iterable[int]) -> str:
    values = sorted({int(page) for page in pages if int(page) > 0})
    if not values:
        return ""
    ranges = []
    start = previous = values[0]
    for value in values[1:]:
        if value == previous + 1:
            previous = value
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = value
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return ",".join(ranges)


def build_resume_state(
    *,
    source_sha256: str,
    config_sha256: str,
    requested_pages: Sequence[int],
    completed_pages: Sequence[int],
    root_collection: str,
    next_stack_offset_m: float,
    aggregate_stats: Dict[str, Any],
    text_delivery_items: Sequence[Dict[str, Any]],
    stacked_page_height_m: float | None = None,
    importer_content_sha256: str | None = None,
) -> Dict[str, Any]:
    source_hash = str(source_sha256 or "").strip().lower()
    config_hash = str(config_sha256 or "").strip().lower()
    if len(source_hash) != 64:
        raise ValueError("source_sha256 must contain 64 hexadecimal characters")
    if len(config_hash) != 64:
        raise ValueError("config_sha256 must contain 64 hexadecimal characters")
    content_hash = importer_content_sha256 or resume_content_sha256()
    if len(content_hash) != 64 or any(char not in "0123456789abcdef" for char in content_hash):
        raise ValueError("importer_content_sha256 must contain 64 hexadecimal characters")
    requested = sorted({int(page) for page in requested_pages if int(page) > 0})
    completed = sorted({int(page) for page in completed_pages if int(page) in requested})
    remaining = [page for page in requested if page not in completed]
    state = {
        "schema": "bcs.blender.page_resume/1.0",
        "source_sha256": source_hash,
        "config_sha256": config_hash,
        "importer_content_sha256": content_hash,
        "requested_pages": requested,
        "completed_pages": completed,
        "remaining_pages": remaining,
        "resume_pages": _page_selector(remaining),
        "root_collection": str(root_collection or ""),
        "next_stack_offset_m": float(next_stack_offset_m or 0.0),
        "aggregate_stats": dict(aggregate_stats or {}),
        "text_delivery_items": [dict(item) for item in text_delivery_items],
    }
    # Height of the last stacked page: the next page is placed from it so a
    # taller page cannot reach into the pages above. Absent until a page is
    # placed, and absent in checkpoints written before it was recorded.
    if stacked_page_height_m is not None:
        state["stacked_page_height_m"] = float(stacked_page_height_m)
    return state


def write_resume_checkpoint(path: str | Path, state: Dict[str, Any]) -> None:
    from .pdfcadcore.atomic_io import atomic_write_text

    target = Path(path)
    atomic_write_text(
        str(target),
        json.dumps(state, indent=2, sort_keys=True, allow_nan=False) + "\n",
    )


def load_resume_checkpoint(
    path: str | Path,
    *,
    source_sha256: str,
    config_sha256: str,
    importer_content_sha256: str | None = None,
) -> Dict[str, Any]:
    state = json.loads(Path(path).read_text(encoding="utf-8"))
    if state.get("schema") != "bcs.blender.page_resume/1.0":
        raise ValueError("unsupported resume checkpoint schema")
    if str(state.get("source_sha256") or "").lower() != str(source_sha256 or "").lower():
        raise ValueError("resume checkpoint source does not match this PDF")
    if str(state.get("config_sha256") or "").lower() != str(config_sha256 or "").lower():
        raise ValueError("resume checkpoint config does not match this import")
    expected_content = importer_content_sha256 or resume_content_sha256()
    if state.get("importer_content_sha256") != expected_content:
        raise ValueError("resume checkpoint importer code has changed or has no code binding; existing geometry and checkpoint were kept")
    return state


__all__ = [
    "ImportCancelledError",
    "build_resume_state",
    "cancel_heartbeat_interval",
    "estimate_import_complexity",
    "load_resume_checkpoint",
    "resume_config_sha256",
    "resume_content_sha256",
    "write_resume_checkpoint",
]
