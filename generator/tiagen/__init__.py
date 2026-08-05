"""tiagen - generate TIA Portal V21 engineering artefacts from a machine spec.

Typical use:

    from tiagen import model, build
    spec = model.load("spec/examples/conveyor-line.yaml")
    result = build.build(spec, out_dir="out")
"""

from . import build, devices, emit_hmi, emit_plan, emit_scl, emit_tags, model, validate  # noqa: F401

__version__ = "0.1.0"

__all__ = [
    "build",
    "devices",
    "emit_hmi",
    "emit_plan",
    "emit_scl",
    "emit_tags",
    "model",
    "validate",
]
