"""SimScale cloud backend plugin for sim-cli.

Importing this package is safe without a SimScale API key. Network calls are
made only by explicit driver operations such as ``sim check``, ``sim connect``,
or ``sim run --solver simscale``.
"""
from importlib.resources import files

from .driver import SimScaleDriver

skills_dir = files(__name__) / "_skills"

plugin_info = {
    "name": "simscale",
    "summary": "SimScale cloud backend driver plugin for sim-cli.",
    "homepage": "https://github.com/svd-ai-lab/sim-plugin-simscale",
    "license_class": "commercial",
    "solver_name": "simscale",
}

__all__ = ["SimScaleDriver", "skills_dir", "plugin_info"]
