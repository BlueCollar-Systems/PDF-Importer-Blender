from types import SimpleNamespace

import pytest

from pdf_vector_importer.image_materials import source_image_sockets


@pytest.mark.parametrize("names", [
    ("Specular", "Emission"),  # Blender 3.6
    ("Specular IOR Level", "Emission Color"),  # Blender 4.0+
])
def test_source_image_controls_resolve_real_host_socket_names(names):
    specular, emission = object(), object()
    shader = SimpleNamespace(inputs=dict(zip(names, (specular, emission), strict=True)))
    assert source_image_sockets(shader) == (specular, emission)


def test_modern_names_take_precedence_when_both_are_exposed():
    inputs = {name: object() for name in (
        "Specular", "Emission", "Specular IOR Level", "Emission Color",
    )}
    assert source_image_sockets(SimpleNamespace(inputs=inputs)) == (
        inputs["Specular IOR Level"], inputs["Emission Color"],
    )


def test_missing_host_controls_are_not_silently_accepted():
    with pytest.raises(KeyError):
        source_image_sockets(SimpleNamespace(inputs={}))
