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


def srgb_channel_to_scene_linear(channel: float) -> float:
    """PDF and preview colors are sRGB. Blender shader colors are scene-linear."""
    value = max(0.0, min(1.0, float(channel)))
    if value <= 0.04045:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def scene_linear_color(color: Color) -> Color:
    """Convert a display sRGB triplet into the linear value Blender must store.

    Assigning a PDF color such as 0.8 red straight into an emission shader
    makes Blender display the sRGB encoding of that number, so the sheet looks
    washed out next to the PDF. Black and white are unchanged.
    """
    return tuple(srgb_channel_to_scene_linear(channel) for channel in color)
