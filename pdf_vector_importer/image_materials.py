"""Principled image-material socket names across maintained Blender hosts."""


def source_image_sockets(shader):
    """Return the same specular/emission controls before and after Blender 4.0."""
    specular = shader.inputs.get("Specular IOR Level")
    if specular is None:
        specular = shader.inputs["Specular"]
    emission = shader.inputs.get("Emission Color")
    if emission is None:
        emission = shader.inputs["Emission"]
    return specular, emission
