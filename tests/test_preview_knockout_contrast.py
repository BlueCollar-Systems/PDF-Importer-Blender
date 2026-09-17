"""White knockout fills must not obscure text in dark preview palettes."""
import pytest

from pdf_vector_importer.visual_style import DARK_PREVIEW_BACKGROUND, preview_color


def luminance(color):
    return sum(c * w for c, w in zip(color, (0.2126, 0.7152, 0.0722)))


@pytest.mark.parametrize("style", ["blueprint", "high_contrast"])
def test_black_note_over_white_pdf_knockout_remains_readable(style):
    note = preview_color((0.0, 0.0, 0.0), style)
    knockout = preview_color((1.0, 1.0, 1.0), style)
    assert knockout == DARK_PREVIEW_BACKGROUND
    assert (luminance(note) + 0.05) / (luminance(knockout) + 0.05) > 7


@pytest.mark.parametrize("style", ["blueprint", "high_contrast"])
def test_inverse_white_source_text_keeps_contrast_with_dark_source_fill(style):
    white_text = preview_color((1.0, 1.0, 1.0), style)
    black_fill = preview_color((0.0, 0.0, 0.0), style)
    assert luminance(white_text) < 0.03
    assert luminance(black_fill) > 0.5


@pytest.mark.parametrize("color", [(0.0, 0.0, 0.0), (1.0, 1.0, 1.0), (0.0156900007, 1.0, 1.0), (0.12, 0.32, 0.71)])
def test_source_accurate_rgb_is_unchanged(color):
    assert preview_color(color, "source") is color


@pytest.mark.parametrize("style", ["blueprint", "high_contrast"])
def test_source_grays_retain_order_in_dark_palette(style):
    values = [luminance(preview_color((v, v, v), style)) for v in (0, .25, .5, .75, 1)]
    assert all(a > b for a, b in zip(values, values[1:]))
