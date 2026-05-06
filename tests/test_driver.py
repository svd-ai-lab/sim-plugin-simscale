from __future__ import annotations

import json
from pathlib import Path

import pytest

import sim_plugin_simscale.driver as drv
from sim_plugin_simscale import SimScaleDriver
from sim_plugin_simscale.api import SimScaleApiError, SimScaleConfig


FIXTURES = Path(__file__).parent / "fixtures"


class FakeClient:
    def __init__(self, *, expensive: bool = False):
        self.config = SimScaleConfig(api_key="test", api_url="https://api.simscale.com")
        self.expensive = expensive
        self.calls: list[str] = []

    def spaces(self):
        self.calls.append("spaces")
        return {"personalSpaces": [{"spaceId": "space-1"}], "teamSpaces": []}

    def projects(self, *, page=1, limit=100):
        self.calls.append("projects")
        return {"_embedded": [], "_meta": {"total": 0}}

    def material_groups(self):
        return {"_embedded": [{"materialGroupId": "group", "groupType": "SIMSCALE_DEFAULT"}]}

    def materials(self, material_group_id):
        return {"_embedded": [{"id": "water", "name": "Water"}]}

    def material(self, material_group_id, material_id):
        return {"id": material_id, "materialGroupId": material_group_id, "name": "Water"}

    def create_storage(self):
        return {"storageId": "storage", "url": "https://upload.example.test/blob"}

    def put_url(self, url, data, *, content_type="application/octet-stream"):
        self.uploaded = (url, data, content_type)
        return b""

    def create_project(self, body):
        self.project_body = body
        return {"projectId": "123"}

    def get_project(self, project_id):
        return {"projectId": project_id, "name": "project"}

    def import_geometry(self, project_id, body):
        self.geometry_import_body = body
        return {"geometryImportId": "gimp", "status": "QUEUED"}

    def get_geometry_import(self, project_id, geometry_import_id):
        return {"geometryImportId": geometry_import_id, "status": "FINISHED", "geometryId": "geom"}

    def geometry_mappings(self, project_id, geometry_id, *, attributes, values):
        mapping = {
            "Fluid Region": "B1_TE39",
            "Face ZMAX": "B1_TE1",
            "Face Junction": "B1_TE2",
            "Face YMAX": "B1_TE3",
        }
        return {"_embedded": [{"name": mapping[values[0]]}]}

    def create_geometry_primitive(self, project_id, body):
        self.point_body = body
        return {"geometryPrimitiveId": "point"}

    def create_simulation(self, project_id, body):
        self.simulation_body = body
        return {"simulationId": "sim"}

    def get_simulation(self, project_id, simulation_id):
        return {"simulationId": simulation_id, "model": {"materials": {"fluids": [{}]}}}

    def update_simulation(self, project_id, simulation_id, body):
        self.updated_simulation = body
        return body

    def update_simulation_materials(self, project_id, simulation_id, body):
        self.material_update_body = body
        return {"ok": True}

    def create_mesh_operation(self, project_id, body):
        self.mesh_body = body
        return {"meshOperationId": "meshop", **body}

    def update_mesh_operation(self, project_id, mesh_operation_id, body):
        return body

    def check_mesh_operation(self, project_id, mesh_operation_id, *, simulation_id):
        return {"entries": []}

    def estimate_mesh_operation(self, project_id, mesh_operation_id):
        value = 99.0 if self.expensive else 0.05
        return {"computeResource": {"type": "CPU_HOURS", "value": value}}

    def start_mesh_operation(self, project_id, mesh_operation_id, *, simulation_id):
        self.calls.append("start_mesh")
        return {"ok": True}

    def get_mesh_operation(self, project_id, mesh_operation_id):
        return {
            "meshOperationId": mesh_operation_id,
            "status": "FINISHED",
            "progress": 1.0,
            "meshId": "mesh",
            "computeResource": {"type": "CPU_HOURS", "value": 0.04},
        }

    def check_simulation(self, project_id, simulation_id):
        return {"entries": []}

    def estimate_simulation(self, project_id, simulation_id):
        return {"computeResource": {"type": "CPU_HOURS", "value": 0.08}}

    def create_simulation_run(self, project_id, simulation_id, body):
        return {"runId": "run", **body}

    def update_simulation_run(self, project_id, simulation_id, run_id, body):
        return body

    def start_simulation_run(self, project_id, simulation_id, run_id):
        self.calls.append("start_run")
        return {"ok": True}

    def get_simulation_run(self, project_id, simulation_id, run_id):
        return {
            "runId": run_id,
            "status": "FINISHED",
            "progress": 1.0,
            "computeResource": {"type": "CPU_HOURS", "value": 0.12},
            "startedAt": "2026-05-06T00:00:00Z",
            "finishedAt": "2026-05-06T00:05:00Z",
        }

    def simulation_run_results(self, project_id, simulation_id, run_id, *, category=None, page=1, limit=100):
        return {
            "_embedded": [
                {
                    "name": "Probe point 1",
                    "quantity": "Ux",
                    "download": {"url": "https://download.example.test/probe.csv"},
                }
            ],
            "_meta": {"total": 1},
        }

    def download_url(self, url):
        return b"Time (s),Point 1\n1,-0.1\n100,-0.05\n"


class TestDetectLint:
    def test_detect_recipe(self):
        assert SimScaleDriver().detect(FIXTURES / "smoke.yaml") is True

    def test_detect_missing_file(self):
        assert SimScaleDriver().detect(FIXTURES / "missing.yaml") is False

    def test_lint_good_recipe(self):
        result = SimScaleDriver().lint(FIXTURES / "smoke.yaml")
        assert result.ok is True

    def test_lint_bad_kind(self):
        result = SimScaleDriver().lint(FIXTURES / "bad_kind.yaml")
        assert result.ok is False
        assert "unsupported" in result.diagnostics[0].message


class TestConnectAndSession:
    def test_connect_no_key(self, monkeypatch):
        monkeypatch.delenv("SIMSCALE_API_KEY", raising=False)
        info = SimScaleDriver().connect()
        assert info.status == "not_installed"
        assert "SIMSCALE_API_KEY" in info.message

    def test_detect_installed_with_api(self, monkeypatch):
        fake = FakeClient()
        monkeypatch.setattr(drv.SimScaleClient, "from_env", lambda: fake)
        installs = SimScaleDriver().detect_installed()
        assert installs[0].name == "simscale"
        assert installs[0].extra["personal_spaces"] == 1

    def test_launch_and_list_projects(self, monkeypatch):
        monkeypatch.setattr(drv.SimScaleClient, "from_env", lambda: FakeClient())
        driver = SimScaleDriver()
        launched = driver.launch(mode="solver", ui_mode="no_gui")
        assert launched["ok"] is True
        result = driver.run(json.dumps({"command": "list_projects"}), "list")
        assert result["ok"] is True
        assert result["projects"]["_meta"]["total"] == 0
        assert driver.query("simscale.projects")["ok"] is True

    def test_unknown_command(self, monkeypatch):
        monkeypatch.setattr(drv.SimScaleClient, "from_env", lambda: FakeClient())
        driver = SimScaleDriver()
        driver.launch()
        result = driver.run(json.dumps({"command": "nope"}))
        assert result["ok"] is False


class TestRecipe:
    def test_run_file_writes_artifacts(self, monkeypatch, tmp_path):
        fake = FakeClient()
        monkeypatch.setattr(drv.SimScaleClient, "from_env", lambda: fake)
        recipe = tmp_path / "recipe.yaml"
        recipe.write_text(
            "kind: pipe_junction_incompressible_smoke\n"
            "name: test\n"
            "max_compute_cpu_hours: 0.5\n"
            f"artifacts_dir: {tmp_path.as_posix()}\n"
            "poll_interval_s: 0\n",
            encoding="utf-8",
        )

        result = SimScaleDriver().run_file(recipe)

        assert result.ok is True
        parsed = json.loads(result.stdout)
        assert parsed["project_id"] == "123"
        assert parsed["status"] == "FINISHED"
        assert (tmp_path / "run" / "probe_points.csv").exists()
        assert "start_mesh" in fake.calls
        assert "start_run" in fake.calls

    def test_estimate_gate_stops_before_compute(self, monkeypatch, tmp_path):
        fake = FakeClient(expensive=True)
        monkeypatch.setattr(drv.SimScaleClient, "from_env", lambda: fake)
        recipe = tmp_path / "recipe.yaml"
        recipe.write_text(
            "kind: pipe_junction_incompressible_smoke\n"
            "max_compute_cpu_hours: 0.5\n"
            f"artifacts_dir: {tmp_path.as_posix()}\n"
            "poll_interval_s: 0\n",
            encoding="utf-8",
        )

        result = SimScaleDriver().run_file(recipe)

        assert result.ok is False
        assert "exceeds limit" in result.errors[0]
        assert "start_mesh" not in fake.calls
        assert "start_run" not in fake.calls


def test_parse_output_last_json_line():
    assert SimScaleDriver().parse_output("log\n{\"ok\": true}\n") == {"ok": True}


def test_api_error_is_safe():
    exc = SimScaleApiError(403, "forbidden", body={"message": "nope"})
    assert "forbidden" in str(exc)
