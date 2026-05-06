from __future__ import annotations

import os
from pathlib import Path

import pytest

from sim_plugin_simscale import SimScaleDriver


@pytest.mark.integration
def test_live_pipe_junction_smoke(tmp_path: Path):
    if os.environ.get("SIMSCALE_RUN_INTEGRATION") != "1":
        pytest.skip("set SIMSCALE_RUN_INTEGRATION=1 to start SimScale cloud compute")
    if not os.environ.get("SIMSCALE_API_KEY"):
        pytest.skip("SIMSCALE_API_KEY is required for live SimScale smoke")

    recipe = tmp_path / "recipe.yaml"
    recipe.write_text(
        "kind: pipe_junction_incompressible_smoke\n"
        "name: sim-plugin-simscale live smoke\n"
        "max_compute_cpu_hours: 0.5\n"
        f"artifacts_dir: {tmp_path.as_posix()}\n",
        encoding="utf-8",
    )

    result = SimScaleDriver().run_file(recipe)

    assert result.ok, result.stderr
