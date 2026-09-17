"""Display palettes that preserve source foreground/background contrast.

PDF white paint often knocks out linework behind a note. Recoloring that
paint almost as brightly as black letters makes the note disappear. Apply
one luminance mapping to both text and geometry so inverse text and opaque
knockouts retain their contrast in the optional dark preview palettes.
"""
from __future__ import annotations

from typing import Tuple

Color = Tuple[float, float, float]
DARK_PREVIEW_BACKGROUND: Color = (0.025, 0.025, 0.025)


def preview_color(color: Color, style: str) -> Color:
    """Retain source RGB exactly, or map source lightness into a dark palette."""
    key = str(style or "source").strip().lower()
    if key not in {"blueprint", "high_contrast"}:
        return color
    foreground = (0.36, 0.74, 0.98) if key == "blueprint" else (0.95, 0.95, 0.95)
    luminance = max(0.0, min(1.0, sum(c * weight for c, weight in zip(
        color, (0.2126, 0.7152, 0.0722), strict=True
    ))))
    return tuple(
        dark + (light - dark) * (1.0 - luminance)
        for dark, light in zip(DARK_PREVIEW_BACKGROUND, foreground, strict=True)
    )
