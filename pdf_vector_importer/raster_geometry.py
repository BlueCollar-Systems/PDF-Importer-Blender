"""Map rendered PDF pixel bounds with the source page affine, not font metrics."""
import math


def source_raster_bounds(item):
    """Cover original glyph quads as well as the renderer's span bbox.

    A font's dictionary bbox can be slightly shorter than its actual source
    glyph quad. Keeping both avoids clipping the top of a rendered glyph while
    leaving the original source bbox and the model's affine mapping unchanged.
    """
    bounds = getattr(item, "source_bbox_pdf", None)
    if bounds is None:
        return None
    x0, y0, x1, y1 = (float(value) for value in bounds)
    points = [(x0, y0), (x1, y1)]
    layouts = getattr(item, "source_char_layout", ()) or ()
    quads = [getattr(char, "source_quad_pdf", None) for char in layouts]
    if not layouts:
        quads = [getattr(item, "source_quad_pdf", None)]
    for quad in quads:
        if quad is None:
            continue
        if len(quad) != 4:
            raise ValueError("Raster source glyph quad must have four corners")
        points.extend((float(x), float(y)) for x, y in quad)
    if not all(math.isfinite(value) for point in points for value in point):
        raise ValueError("Raster source glyph coverage is not finite")
    return (min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points))


def source_bbox_to_model(item, bounds=None):
    source = getattr(item, "source_quad_pdf", None)
    target = getattr(item, "target_quad_model", None)
    bounds = bounds if bounds is not None else getattr(item, "source_bbox_pdf", None)
    if source is None or target is None or bounds is None:
        return tuple(item.bbox)
    s0, s1, _, s3 = source
    t0, t1, _, t3 = target
    ux, uy = s1[0]-s0[0], s1[1]-s0[1]
    vx, vy = s3[0]-s0[0], s3[1]-s0[1]
    determinant = ux*vy-uy*vx
    if not math.isfinite(determinant) or abs(determinant) < 1e-12:
        raise ValueError("Source text quad cannot establish raster page placement")
    points = []
    x0, y0, x1, y1 = bounds
    for x, y in ((x0,y0), (x1,y0), (x1,y1), (x0,y1)):
        dx, dy = x-s0[0], y-s0[1]
        u, v = (dx*vy-dy*vx)/determinant, (ux*dy-uy*dx)/determinant
        points.append((t0[0]+u*(t1[0]-t0[0])+v*(t3[0]-t0[0]),
                       t0[1]+u*(t1[1]-t0[1])+v*(t3[1]-t0[1])))
    if not all(math.isfinite(value) for point in points for value in point):
        raise ValueError("Raster page placement is not finite")
    return (min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points))
