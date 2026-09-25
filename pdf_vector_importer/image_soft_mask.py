"""Compose independent, non-interpolated PDF image/soft-mask sample grids.

ISO 32000-1 Table 145 maps both images to the unit square and permits unequal
dimensions when Matte is absent. Integer replication to their least common
grid preserves every original sample boundary; resizing to the larger image
would move boundaries when the dimensions are not integer multiples.
"""
from __future__ import annotations

from math import lcm
import re


MAX_ALIGNED_SAMPLE_BYTES = 32 * 1024 * 1024
MAX_ALIGNED_DIMENSION = 8192


def common_sample_grid(width, height, mask_width, mask_height, channels,
                       *, max_bytes=MAX_ALIGNED_SAMPLE_BYTES, max_dimension=MAX_ALIGNED_DIMENSION):
    dimensions = (width, height, mask_width, mask_height, channels)
    if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0
           for value in dimensions):
        raise ValueError("image and soft-mask sample dimensions must be positive integers")
    common_width = lcm(width, mask_width)
    common_height = lcm(height, mask_height)
    if max(common_width, common_height) > max_dimension:
        raise ValueError(
            f"exact image soft-mask grid {common_width}x{common_height} "
            f"exceeds the {max_dimension}-pixel dimension bound"
        )
    # Expanded byte buffers, constructor copies, composed alpha pixmap and
    # replication/join scratch space. Check before building any expanded data.
    required = common_width * common_height * (4 * channels + 4)
    if required > max_bytes:
        raise ValueError(
            f"exact image soft-mask grid {common_width}x{common_height} "
            f"requires {required} sample bytes, exceeding the {max_bytes}-byte bound"
        )
    return common_width, common_height


def repeat_sample_grid(samples, width, height, channels, target_width, target_height):
    """Replicate decoded cells exactly, with no interpolation or edge cropping."""
    if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0
           for value in (width, height, channels, target_width, target_height)):
        raise ValueError("sample dimensions must be positive integers")
    if target_width % width or target_height % height:
        raise ValueError("target sample grid must be an integer multiple of the source")
    if len(samples) != width * height * channels:
        raise ValueError("image sample bytes do not match the declared dimensions")
    repeat_x, repeat_y = target_width // width, target_height // height
    stride = width * channels
    rows = []
    for y in range(height):
        row = samples[y * stride:(y + 1) * stride]
        expanded = b"".join(row[x:x + channels] * repeat_x
                            for x in range(0, stride, channels))
        rows.append(expanded * repeat_y)
    return b"".join(rows)


def _non_interpolated(doc, xref):
    kind, value = doc.xref_get_key(xref, "Interpolate")
    visited = set()
    while kind == "xref":
        match = re.fullmatch(r"(\d+)\s+\d+\s+R", value.strip())
        if match is None or len(visited) >= 16:
            raise ValueError(f"image xref {xref} has an invalid Interpolate reference")
        target = int(match.group(1))
        if target in visited:
            raise ValueError(f"image xref {xref} has a cyclic Interpolate reference")
        visited.add(target)
        value = doc.xref_object(target, compressed=True).strip()
        kind = "bool" if value in {"true", "false"} else "null" if value == "null" else "xref"
    if kind == "null" or (kind == "bool" and value == "false"):
        return True
    if kind == "bool" and value == "true":
        return False
    raise ValueError(f"image xref {xref} has an invalid Interpolate value")


def combine_image_soft_mask(fitz, doc, xref, mask_xref, image, mask):
    """Retain equal-size behavior; align only proven non-interpolated grids."""
    if (image.width, image.height) == (mask.width, mask.height):
        return fitz.Pixmap(image, mask)
    if doc.xref_get_key(mask_xref, "Matte")[0] != "null":
        raise ValueError("a soft mask with Matte must have the parent image dimensions")
    if not _non_interpolated(doc, xref) or not _non_interpolated(doc, mask_xref):
        raise ValueError("unequal interpolated image soft-mask grids require a verified compositor")
    if doc.xref_get_key(mask_xref, "ColorSpace") != ("name", "/DeviceGray"):
        raise ValueError("soft-mask alignment requires an explicit DeviceGray mask")
    if image.alpha or mask.alpha or mask.n != 1 or mask.colorspace is None or mask.colorspace.n != 1:
        raise ValueError("soft-mask alignment requires opaque color samples and a decoded gray mask")
    width, height = common_sample_grid(image.width, image.height, mask.width, mask.height, image.n)
    color_samples = repeat_sample_grid(image.samples, image.width, image.height, image.n, width, height)
    mask_samples = repeat_sample_grid(mask.samples, mask.width, mask.height, 1, width, height)
    aligned_color = fitz.Pixmap(image.colorspace, width, height, color_samples, False)
    aligned_mask = fitz.Pixmap(mask.colorspace, width, height, mask_samples, False)
    return fitz.Pixmap(aligned_color, aligned_mask)
