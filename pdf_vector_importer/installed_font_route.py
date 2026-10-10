"""Use an installed font only when the PDF's own widths prove it is the same font.

Most CAD and Tekla drawings name Arial without storing it inside the PDF. The
extraction then holds a positive absence proof ("embedded font stream is
empty") and, without a font program, Text and 3D Text step down to letter
outlines.

This module gives such a font an installed face only when the PDF itself
proves the match, once per font per page:

- the font dictionary is a simple TrueType or Type1 font,
- its encoding is WinAnsi (or absent) with no ``/Differences``,
- exactly one installed face carries the declared name and style,
- every declared width the face can be checked against is within 1/1000 em of
  that face's own advance, and
- the face has every character the page draws with that font.

Any failed check changes nothing: the item keeps today's route. A proven item
is handed to the builder as a copy carrying the installed face, while the
original item (with its absence proof) stays reachable through
:func:`installed_font_absence_item`, so a per-item attempt that does not verify
can be cleaned up and retried exactly as before.

The shared core (pdfcadcore) is only read here, never changed.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from hashlib import sha256
from io import BytesIO
import math
import os
import re
from typing import Any, Dict, List, Optional, Tuple

INSTALLED_FONT_ORIGIN = "installed_face_widths_matched"
# A PDF writer stores each width as a whole number of 1/1000 em, rounded or
# truncated from the face's exact advance, so the two never differ by more
# than one unit for the same face.
WIDTH_TOLERANCE_THOUSANDTHS_EM = 1.0

_ABSENCE_REASON = "embedded_font_asset_build_failed"
_ABSENCE_DETAIL = "embedded font stream is empty"
_ABSENCE_CATEGORY = "source_specific_impossibility"
_ABSENCE_ITEM_ATTRIBUTE = "_installed_font_absence_item"
_SIMPLE_SUBTYPES = ("/TrueType", "/Type1")
_COLLECTION_SUFFIXES = (".ttc", ".otc")
_SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")
_NAME_ESCAPE = re.compile(r"#([0-9A-Fa-f]{2})")
_FACE_CACHE: Dict[Tuple[str, int, int], "_InstalledFace"] = {}
_FACE_CACHE_LIMIT = 4
_REPORT_CHECK_LIMIT = 200


@dataclass(frozen=True)
class InstalledFontAsset:
    """The fields the Blender text builder reads from an exact font asset.

    Field names match ``pdfcadcore.embedded_fonts.EmbeddedFontAsset`` so the
    builder treats both alike; the extra fields record why this installed face
    was allowed to stand in for a font the PDF names but does not carry.
    """

    page_number: int
    span_font_name: str
    base_font_name: str
    source_xref: int
    resource_name: str
    source_font_type: str
    source_encoding: str
    source_format: str
    source_origin: str
    source_bytes: bytes = field(repr=False, compare=False)
    source_sha256: str
    usable_format: str
    usable_bytes: bytes = field(repr=False, compare=False)
    usable_sha256: str
    asset_id: str
    unicode_map_installed: bool = False
    units_per_em: int = 0
    ascender: int = 0
    descender: int = 0
    glyph_advances: Tuple[int, ...] = field(default=(), repr=False)
    source_binding_method: str = INSTALLED_FONT_ORIGIN
    source_program_candidates: Tuple[Tuple[int, str], ...] = ()
    face_path: str = ""
    face_sha256: str = ""
    widths_checked: int = 0
    widths_declared: int = 0
    worst_width_delta: float = 0.0
    width_tolerance: float = WIDTH_TOLERANCE_THOUSANDTHS_EM


@dataclass(frozen=True)
class _InstalledFace:
    path: str
    data: bytes = field(repr=False)
    sha256: str
    source_format: str
    units_per_em: int
    ascender: int
    descender: int
    glyph_advances: Tuple[int, ...] = field(repr=False)
    cmap: Dict[int, int] = field(repr=False)


class _NotProven(Exception):
    """One check failed; the font keeps today's route. The message says which."""


# ── public helpers ──


def is_installed_font_asset(asset: Any) -> bool:
    return str(getattr(asset, "source_origin", "") or "") == INSTALLED_FONT_ORIGIN


def installed_font_absence_item(text_item: Any):
    """The original item (absence proof intact) behind an installed-face item."""
    if not is_installed_font_asset(getattr(text_item, "font_asset", None)):
        return None
    return getattr(text_item, _ABSENCE_ITEM_ATTRIBUTE, None)


def installed_font_evidence(text_item: Any) -> Dict[str, Any]:
    """Per-item report evidence for a span drawn with a width-matched installed face."""
    asset = getattr(text_item, "font_asset", None)
    if not is_installed_font_asset(asset):
        return {}
    checked = int(getattr(asset, "widths_checked", 0) or 0)
    evidence: Dict[str, Any] = {
        "font_source": "installed",
        "font_source_rule": "installed face used because every PDF width matched it",
        "widths_matched": f"{checked}/{checked}",
        "widths_declared": int(getattr(asset, "widths_declared", 0) or 0),
        "worst_width_delta_thousandths_em": float(
            getattr(asset, "worst_width_delta", 0.0) or 0.0
        ),
        "width_tolerance_thousandths_em": float(
            getattr(asset, "width_tolerance", WIDTH_TOLERANCE_THOUSANDTHS_EM)
        ),
        "installed_face_file": os.path.basename(str(getattr(asset, "face_path", "") or "")),
        "installed_face_sha256": str(getattr(asset, "face_sha256", "") or ""),
    }
    failure = getattr(text_item, "font_failure", None)
    if failure is not None:
        evidence["source_font_absence"] = {
            "reason": str(getattr(failure, "reason", "") or ""),
            "detail": str(getattr(failure, "detail", "") or ""),
            "proof_category": str(getattr(failure, "proof_category", "") or ""),
            "source_xref": getattr(failure, "source_xref", None),
        }
    return evidence


# ── the PDF's own declarations ──


def _absence_xref(item: Any, page_number: int) -> Optional[int]:
    """The font xref when extraction positively proved the program is absent."""
    if getattr(item, "font_asset", None) is not None:
        return None
    failure = getattr(item, "font_failure", None)
    if failure is None:
        return None
    if (
        getattr(failure, "reason", "") != _ABSENCE_REASON
        or getattr(failure, "detail", "") != _ABSENCE_DETAIL
        or getattr(failure, "proof_category", "") != _ABSENCE_CATEGORY
        or getattr(failure, "page_number", None) != page_number
        or getattr(failure, "span_font_name", None) != getattr(item, "font_name", None)
    ):
        return None
    xref = getattr(failure, "source_xref", None)
    if type(xref) is not int or xref <= 0:
        return None
    return xref


def _decoded_name(value: Any) -> str:
    text = str(value or "").strip().lstrip("/")
    text = _NAME_ESCAPE.sub(lambda match: chr(int(match.group(1), 16)), text)
    return _SUBSET_PREFIX.sub("", text).strip()


def _winansi_text(code: int) -> Optional[str]:
    try:
        text = bytes((int(code),)).decode("cp1252")
    except (UnicodeDecodeError, ValueError, OverflowError):
        return None
    return text if len(text) == 1 else None


def _winansi_code(text: str) -> Optional[int]:
    try:
        data = str(text).encode("cp1252")
    except (UnicodeEncodeError, ValueError):
        return None
    return data[0] if len(data) == 1 else None


# With no /Encoding a simple font falls back to its own built-in encoding.
# Only printable ASCII codes mean the same character in every built-in Latin
# encoding; the two quote positions differ between Standard and WinAnsi.
_ASCII_SAFE = frozenset(range(0x20, 0x7F)) - {0x27, 0x60}


def _ascii_text(code: int) -> Optional[str]:
    return chr(code) if int(code) in _ASCII_SAFE else None


def _ascii_code(text: str) -> Optional[int]:
    code = ord(text) if len(str(text)) == 1 else -1
    return code if code in _ASCII_SAFE else None


def _font_dictionary(document, xref: int) -> Dict[str, Any]:
    from .pdfcadcore import glyph_code_recovery as gcr

    kind, subtype = gcr._xref_key(document, xref, "Subtype")
    if kind != "name" or subtype not in _SIMPLE_SUBTYPES:
        raise _NotProven(f"the font is not a simple TrueType or Type1 font ({subtype or 'no subtype'})")
    kind, encoding = gcr._resolved_key(document, xref, "Encoding")
    if kind is None:
        encoding_name = ""
        code_to_text, text_to_code = _ascii_text, _ascii_code
    elif kind == "name" and encoding == "/WinAnsiEncoding":
        encoding_name = "WinAnsiEncoding"
        code_to_text, text_to_code = _winansi_text, _winansi_code
    elif (
        kind in ("dict", "object")
        and "Differences" not in str(encoding)
        and re.search(r"/BaseEncoding\s*/WinAnsiEncoding\b", str(encoding))
    ):
        encoding_name = "WinAnsiEncoding"
        code_to_text, text_to_code = _winansi_text, _winansi_code
    else:
        raise _NotProven("the font's encoding is not plain WinAnsi (it changes some letters)")
    kind, first_text = gcr._xref_key(document, xref, "FirstChar")
    try:
        first_char = int(float(first_text))
    except (TypeError, ValueError):
        raise _NotProven("the font has no FirstChar for its widths") from None
    kind, widths_text = gcr._resolved_key(document, xref, "Widths")
    if not widths_text or "[" not in str(widths_text):
        raise _NotProven("the font has no Widths array")
    # A simple /Widths array starts at FirstChar; written as one CID-style run
    # the shared parser reads it without guessing at ranges.
    widths = gcr._parse_width_array(f"[{first_char} {widths_text}]")
    if not widths:
        raise _NotProven("the font's Widths array is empty")
    kind, base_font = gcr._xref_key(document, xref, "BaseFont")
    italic, bold = gcr._descriptor_style(document, xref)
    return {
        "subtype": str(subtype).lstrip("/"),
        "encoding": encoding_name,
        "code_to_text": code_to_text,
        "text_to_code": text_to_code,
        "widths": widths,
        "base_font": _decoded_name(base_font),
        "italic": italic,
        "bold": bold,
    }


# ── the installed face ──


def _load_face(path: str) -> _InstalledFace:
    from .pdfcadcore.embedded_fonts import (
        ExactFontSourceImpossible,
        _font_delivery_metrics,
        _validate_font_work_bounds,
    )

    try:
        status = os.stat(path)
    except OSError as exc:
        raise _NotProven(f"the installed face cannot be read ({type(exc).__name__})") from None
    key = (os.path.normcase(os.path.abspath(path)), int(status.st_size), int(status.st_mtime_ns))
    cached = _FACE_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        with open(path, "rb") as handle:
            data = handle.read()
        _validate_font_work_bounds(data)
        units_per_em, ascender, descender, advances = _font_delivery_metrics(data)
        from fontTools.ttLib import TTFont

        font = TTFont(BytesIO(data), lazy=True, fontNumber=0)
        try:
            cmap = {
                int(codepoint): int(font.getGlyphID(name))
                for codepoint, name in (font.getBestCmap() or {}).items()
            }
            source_format = "otf" if "CFF " in font or "CFF2" in font else "ttf"
        finally:
            font.close()
    except (ExactFontSourceImpossible, ImportError, OSError, KeyError, ValueError,
            TypeError, AttributeError, AssertionError, EOFError) as exc:
        raise _NotProven(
            f"the installed face cannot be used ({type(exc).__name__})"
        ) from None
    if not cmap:
        raise _NotProven("the installed face has no character map")
    face = _InstalledFace(
        path=str(path),
        data=data,
        sha256=sha256(data).hexdigest(),
        source_format=source_format,
        units_per_em=int(units_per_em),
        ascender=int(ascender),
        descender=int(descender),
        glyph_advances=tuple(advances),
        cmap=cmap,
    )
    while len(_FACE_CACHE) >= _FACE_CACHE_LIMIT:
        _FACE_CACHE.pop(next(iter(_FACE_CACHE)))
    _FACE_CACHE[key] = face
    return face


def clear_installed_face_cache() -> None:
    _FACE_CACHE.clear()


def _single_installed_face(font_name: str, italic, bold) -> str:
    from .pdfcadcore import glyph_code_recovery as gcr

    style = gcr._face_style(font_name, italic_hint=italic, bold_hint=bold)
    paths, _indexed = gcr._reference_faces_for(style)
    paths = tuple(
        path for path in paths
        if not str(path).lower().endswith(_COLLECTION_SUFFIXES)
    )
    if not paths:
        raise _NotProven(f"no installed font is named {style.describe()}")
    if len(paths) != 1:
        raise _NotProven(
            f"{len(paths)} installed fonts are named {style.describe()}; the match must be unique"
        )
    return str(paths[0])


def _width_proof(facts: Dict[str, Any], face: _InstalledFace) -> Tuple[set, int, int, float]:
    checked_codes = set()
    declared = 0
    worst = 0.0
    tolerance = WIDTH_TOLERANCE_THOUSANDTHS_EM + 1e-9
    for code in sorted(facts["widths"]):
        width = float(facts["widths"][code])
        if not math.isfinite(width):
            raise _NotProven(f"the PDF width for code {code} is not a number")
        if width == 0.0:
            continue
        declared += 1
        text = facts["code_to_text"](code)
        glyph_id = face.cmap.get(ord(text)) if text else None
        if glyph_id is None or glyph_id >= len(face.glyph_advances):
            continue  # not a character this face can draw; it cannot be used below
        expected = math.floor(
            face.glyph_advances[glyph_id] * 1000.0 / float(face.units_per_em)
        )
        delta = abs(width - expected)
        if delta > tolerance:
            raise _NotProven(
                f"the PDF width for {text!r} is {width:g} but the installed face "
                f"gives {expected} (1/1000 em)"
            )
        checked_codes.add(int(code))
        worst = max(worst, delta)
    if not checked_codes:
        raise _NotProven("no PDF width could be checked against the installed face")
    return checked_codes, len(checked_codes), declared, worst


def _page_font_records(page) -> Tuple[Tuple[int, str, str], ...]:
    try:
        records = tuple(page.get_fonts(full=True))
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return ()
    rows = []
    for record in records:
        try:
            rows.append((int(record[0]), _decoded_name(record[3]), str(record[4] or "")))
        except (IndexError, TypeError, ValueError):
            continue
    return tuple(rows)


def _prove_font(document, page_records, font_name: str, absence_xrefs, used_text) -> Dict[str, Any]:
    """Prove one font name on one page, or raise _NotProven with the reason."""
    xrefs = sorted({xref for xref, name, _res in page_records if name == font_name})
    if not xrefs:
        xrefs = sorted(absence_xrefs)
    elif not set(absence_xrefs) <= set(xrefs):
        raise _NotProven("the font named by the text is not in the page's font list")
    face_path = None
    proofs = []
    for xref in xrefs:
        facts = _font_dictionary(document, xref)
        if facts["base_font"] != font_name:
            raise _NotProven(
                f"the font dictionary names {facts['base_font']!r}, not {font_name!r}"
            )
        path = _single_installed_face(font_name, facts["italic"], facts["bold"])
        if face_path is not None and os.path.normcase(path) != os.path.normcase(face_path):
            raise _NotProven("two fonts with this name on the page match different installed faces")
        face_path = path
        face = _load_face(path)
        checked_codes, checked, declared, worst = _width_proof(facts, face)
        for text in used_text:
            code = facts["text_to_code"](text)
            if code is None or code not in checked_codes:
                raise _NotProven(
                    f"the page draws {text!r}, which the width check could not cover"
                )
            if face.cmap.get(ord(text)) is None:
                raise _NotProven(f"the installed face has no {text!r}")
        proofs.append((xref, facts, face, checked, declared, worst))
    return {
        "face": proofs[0][2],
        "proofs": proofs,
        "widths_checked": min(proof[3] for proof in proofs),
        "widths_declared": max(proof[4] for proof in proofs),
        "worst_width_delta": max(proof[5] for proof in proofs),
    }


def _asset_for(page_number: int, font_name: str, proof: Dict[str, Any], resource_names) -> InstalledFontAsset:
    face = proof["face"]
    xref, facts = proof["proofs"][0][0], proof["proofs"][0][1]
    candidates = tuple(sorted(
        (int(x), str(resource_names.get(int(x), "")))
        for x, *_rest in proof["proofs"]
    ))
    return InstalledFontAsset(
        page_number=int(page_number),
        span_font_name=str(font_name),
        base_font_name=str(font_name),
        source_xref=int(xref),
        resource_name=str(resource_names.get(int(xref), "")),
        source_font_type=str(facts["subtype"]),
        source_encoding=str(facts["encoding"]),
        source_format=face.source_format,
        source_origin=INSTALLED_FONT_ORIGIN,
        source_bytes=face.data,
        source_sha256=face.sha256,
        usable_format=face.source_format,
        usable_bytes=face.data,
        usable_sha256=face.sha256,
        asset_id=f"sha256:{face.sha256}",
        unicode_map_installed=False,
        units_per_em=face.units_per_em,
        ascender=face.ascender,
        descender=face.descender,
        glyph_advances=face.glyph_advances,
        source_binding_method=INSTALLED_FONT_ORIGIN,
        source_program_candidates=candidates,
        face_path=face.path,
        face_sha256=face.sha256,
        widths_checked=int(proof["widths_checked"]),
        widths_declared=int(proof["widths_declared"]),
        worst_width_delta=float(proof["worst_width_delta"]),
    )


def _installed_item(item: Any, asset: InstalledFontAsset, face: _InstalledFace):
    layouts = tuple(getattr(item, "source_char_layout", ()) or ())
    remapped = []
    for layout in layouts:
        text = str(getattr(layout, "text", "") or "")
        glyph_id = face.cmap.get(ord(text)) if len(text) == 1 else None
        if glyph_id is None:
            return None
        # Today's glyph ids belong to the renderer's stand-in font; the
        # installed face numbers its glyphs differently.
        remapped.append(replace(layout, glyph_id=int(glyph_id)))
    twin = replace(item, font_asset=asset, source_char_layout=tuple(remapped))
    setattr(twin, _ABSENCE_ITEM_ATTRIBUTE, item)
    return twin


def attach_installed_fonts(document, page, text_items, page_number: int, stats=None) -> List[Any]:
    """Return the page's text items with width-proven installed faces attached.

    The input list and its items are never changed: proven items are replaced
    in the returned list by copies carrying the installed face. Anything not
    proven is returned as it was.
    """
    items = list(text_items or ())
    page_number = int(page_number)
    if document is None:
        document = getattr(page, "parent", None)
    by_font: Dict[str, List[int]] = {}
    absence_xrefs: Dict[str, set] = {}
    for index, item in enumerate(items):
        xref = _absence_xref(item, page_number)
        if xref is None:
            continue
        text = str(getattr(item, "text", "") or "")
        layouts = tuple(getattr(item, "source_char_layout", ()) or ())
        # Whitespace-only spans keep their proven zero-ink route.
        if not text.strip() or not layouts or "".join(
            str(getattr(layout, "text", "") or "") for layout in layouts
        ) != text:
            continue
        name = str(getattr(item, "font_name", "") or "")
        by_font.setdefault(name, []).append(index)
        absence_xrefs.setdefault(name, set()).add(xref)
    if not by_font or document is None:
        return items
    page_records = _page_font_records(page)
    resource_names = {xref: resource for xref, _name, resource in page_records}
    attached = 0
    checks = []
    for font_name in sorted(by_font):
        indices = by_font[font_name]
        used_text = sorted({
            str(layout.text)
            for index in indices
            for layout in items[index].source_char_layout
        })
        check: Dict[str, Any] = {
            "page": page_number,
            "font_name": font_name,
            "items": len(indices),
        }
        try:
            proof = _prove_font(
                document, page_records, font_name, absence_xrefs[font_name], used_text
            )
            asset = _asset_for(page_number, font_name, proof, resource_names)
        except _NotProven as exc:
            check.update(status="not_used", reason=str(exc))
            checks.append(check)
            continue
        except Exception as exc:  # an unexpected PDF shape keeps today's route
            check.update(
                status="not_used",
                reason=f"the check could not finish ({type(exc).__name__})",
            )
            checks.append(check)
            continue
        used = 0
        for index in indices:
            twin = _installed_item(items[index], asset, proof["face"])
            if twin is not None:
                items[index] = twin
                used += 1
        attached += used
        check.update(
            status="used" if used else "not_used",
            reason=(
                "every PDF width matched the installed face"
                if used else "no text item could be mapped to the installed face"
            ),
            items_attached=used,
            installed_face_file=os.path.basename(asset.face_path),
            widths_matched=f"{asset.widths_checked}/{asset.widths_checked}",
            widths_declared=asset.widths_declared,
            worst_width_delta_thousandths_em=asset.worst_width_delta,
            source_xrefs=[int(x) for x, *_rest in proof["proofs"]],
        )
        checks.append(check)
    if isinstance(stats, dict):
        stats["installed_font_items"] = int(stats.get("installed_font_items", 0) or 0) + attached
        recorded = stats.setdefault("installed_font_checks", [])
        if isinstance(recorded, list):
            room = max(0, _REPORT_CHECK_LIMIT - len(recorded))
            recorded.extend(checks[:room])
    return items


def installed_font_report(stats: Dict[str, Any], delivery_records) -> Optional[Dict[str, Any]]:
    """Report block: how many spans used an installed face, and why or why not."""
    checks = list((stats or {}).get("installed_font_checks") or [])
    attached = int((stats or {}).get("installed_font_items", 0) or 0)
    if not checks and not attached:
        return None
    delivered = 0
    stepped_back = 0
    for record in delivery_records or ():
        if not isinstance(record, dict):
            continue
        attempts = list(record.get("attempts") or ())
        final = attempts[-1] if attempts else {}
        evidence = dict(final.get("evidence") or {}) if isinstance(final, dict) else {}
        if record.get("status") == "delivered" and evidence.get("font_source") == "installed":
            delivered += 1
        elif any(
            isinstance(attempt, dict)
            and isinstance(attempt.get("evidence"), dict)
            and "installed_font_attempt" in attempt["evidence"]
            for attempt in attempts
        ):
            stepped_back += 1
    return {
        "rule": (
            "A font the PDF names but does not carry is drawn with the installed "
            "font of that name only when every width in the PDF matches it."
        ),
        "items_with_installed_font": attached,
        "items_delivered_with_installed_font": delivered,
        "items_back_on_previous_route": stepped_back,
        "checks": checks,
    }


__all__ = [
    "INSTALLED_FONT_ORIGIN",
    "InstalledFontAsset",
    "WIDTH_TOLERANCE_THOUSANDTHS_EM",
    "attach_installed_fonts",
    "clear_installed_face_cache",
    "installed_font_absence_item",
    "installed_font_evidence",
    "installed_font_report",
    "is_installed_font_asset",
]
