"""sim-cli driver for SimScale cloud workflows."""
from __future__ import annotations

import json
import os
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml
from sim.driver import ConnectionInfo, Diagnostic, LintResult, RunResult, SolverInstall

from .api import SimScaleApiError, SimScaleClient, SimScaleConfig


TERMINAL_STATUSES = {"FINISHED", "CANCELED", "FAILED"}
DEFAULT_MAX_COMPUTE_CPU_HOURS = 0.5


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _embedded(payload: dict | None) -> list:
    if not isinstance(payload, dict):
        return []
    value = payload.get("_embedded", payload.get("embedded", []))
    return value if isinstance(value, list) else []


def _meta_total(payload: dict | None) -> int | None:
    if not isinstance(payload, dict):
        return None
    meta = payload.get("_meta") or payload.get("meta") or {}
    total = meta.get("total")
    return int(total) if isinstance(total, int) else None


def _json_default(value: Any) -> str:
    return str(value)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=_json_default), encoding="utf-8")


def _load_recipe(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("recipe must be a YAML mapping")
    return data


def _resource_value(estimate: dict) -> float | None:
    resource = estimate.get("computeResource") or estimate.get("compute_resource") or {}
    value = resource.get("value")
    return float(value) if isinstance(value, (int, float)) else None


def _find_default_water(client: SimScaleClient) -> tuple[str, str, dict]:
    groups = _embedded(client.material_groups())
    group = next((g for g in groups if g.get("groupType") == "SIMSCALE_DEFAULT"), None)
    if not group:
        raise RuntimeError("SimScale default material group was not found")
    group_id = group["materialGroupId"]
    materials = _embedded(client.materials(group_id))
    water = next((m for m in materials if m.get("name") == "Water"), None)
    if not water:
        raise RuntimeError("SimScale default Water material was not found")
    material_id = water["id"]
    return group_id, material_id, client.material(group_id, material_id)


def _geometry_import_payload(storage_id: str) -> dict:
    return {
        "name": "CAD-pipe-junction_v1",
        "location": {"storageId": storage_id},
        "format": "PARASOLID",
        "inputUnit": "m",
        "options": {
            "facetSplit": False,
            "sewing": False,
            "improve": True,
            "optimizeForLBMSolver": False,
        },
    }


def _probe_point_payload() -> dict:
    return {
        "name": "Point 1",
        "type": "POINT",
        "center": {
            "unit": "m",
            "value": {
                "x": 0.0035744310360600745,
                "y": 0.4499999880790711,
                "z": -0.4507558972231502,
            },
        },
    }


def _simulation_payload(name: str, geometry_id: str, entities: dict[str, str], point_uuid: str) -> dict:
    return {
        "name": name,
        "version": "31.0",
        "geometryId": geometry_id,
        "model": {
            "type": "INCOMPRESSIBLE",
            "model": {},
            "initialConditions": {},
            "advancedConcepts": {},
            "materials": {},
            "numerics": {
                "relaxationFactor": {},
                "pressureReferenceValue": {"value": 0, "unit": "Pa"},
                "residualControls": {
                    "velocity": {},
                    "pressure": {},
                    "turbulentKineticEnergy": {},
                    "omegaDissipationRate": {},
                },
                "solvers": {},
                "schemes": {
                    "timeDifferentiation": {},
                    "gradient": {},
                    "divergence": {},
                    "laplacian": {},
                    "interpolation": {},
                    "surfaceNormalGradient": {},
                },
            },
            "boundaryConditions": [
                {
                    "type": "VELOCITY_INLET_V3",
                    "name": "Velocity inlet 1",
                    "topologicalReference": {"entities": [entities["inlet1"]]},
                    "velocity": {
                        "type": "FIXED_VALUE",
                        "value": {
                            "value": {
                                "type": "COMPONENT",
                                "x": {"type": "CONSTANT", "value": 0},
                                "y": {"type": "CONSTANT", "value": 0},
                                "z": {"type": "CONSTANT", "value": -1.5},
                            }
                        },
                    },
                },
                {
                    "type": "VELOCITY_INLET_V3",
                    "name": "Velocity inlet 2",
                    "topologicalReference": {"entities": [entities["inlet2"]]},
                    "velocity": {
                        "type": "FIXED_VALUE",
                        "value": {
                            "value": {
                                "type": "COMPONENT",
                                "x": {"type": "CONSTANT", "value": 0},
                                "y": {"type": "CONSTANT", "value": -1},
                                "z": {"type": "CONSTANT", "value": 0},
                            }
                        },
                    },
                },
                {
                    "type": "PRESSURE_OUTLET_V30",
                    "name": "Pressure outlet 3",
                    "topologicalReference": {"entities": [entities["outlet"]]},
                    "gaugePressure": {
                        "type": "FIXED_VALUE",
                        "value": {"value": {"type": "CONSTANT", "value": 0}, "unit": "Pa"},
                    },
                },
            ],
            "simulationControl": {
                "endTime": {"value": 100, "unit": "s"},
                "deltaT": {"value": 1, "unit": "s"},
                "writeControl": {"type": "TIME_STEP", "writeInterval": 20},
                "maxRunTime": {"value": 10000, "unit": "s"},
                "decomposeAlgorithm": {"type": "SCOTCH"},
            },
            "resultControl": {
                "probePoints": [
                    {
                        "type": "PROBE_POINTS",
                        "name": "Probe point 1",
                        "writeControl": {"type": "TIME_STEP", "writeInterval": 1},
                        "geometryPrimitiveUuids": [point_uuid],
                    }
                ]
            },
        },
    }


def _mesh_operation_payload(geometry_id: str) -> dict:
    return {
        "name": "Pipe junction mesh",
        "version": "9.0",
        "geometryId": geometry_id,
        "model": {
            "type": "SIMMETRIX_MESHING_FLUID_V16",
            "physicsBasedMeshing": True,
            "automaticLayerSettings": {"type": "AUTOMATIC_LAYER_ON"},
        },
    }


@dataclass
class _RunContext:
    client: SimScaleClient
    recipe: dict
    artifacts_dir: Path
    events: list[dict]

    @property
    def poll_interval_s(self) -> float:
        value = self.recipe.get("poll_interval_s", os.environ.get("SIMSCALE_POLL_INTERVAL", 30))
        return float(value)

    @property
    def max_compute_cpu_hours(self) -> float:
        return float(self.recipe.get("max_compute_cpu_hours", DEFAULT_MAX_COMPUTE_CPU_HOURS))

    def event(self, stage: str, **fields: Any) -> None:
        self.events.append({"at": _utc_now(), "stage": stage, **fields})


class SimScaleDriver:
    """SimScale cloud driver with one guarded MVP recipe."""

    name = "simscale"
    supports_session = True

    def __init__(self):
        self._client: SimScaleClient | None = None
        self._session_id: str | None = None
        self._connected_at: float | None = None
        self._run_count = 0
        self._spaces: dict | None = None
        self._projects: dict | None = None
        self._last_result: dict | None = None
        self._last_project_id: str | None = None
        self._last_simulation_id: str | None = None
        self._last_run_id: str | None = None

    def detect(self, script: Path) -> bool:
        try:
            if script.suffix.lower() not in {".yaml", ".yml"}:
                return False
            text = script.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return False
        return "pipe_junction_incompressible_smoke" in text or "simscale" in text.lower()

    def lint(self, script: Path) -> LintResult:
        try:
            recipe = _load_recipe(script)
        except FileNotFoundError:
            return LintResult(False, [Diagnostic("error", f"recipe not found: {script}")])
        except Exception as exc:
            return LintResult(False, [Diagnostic("error", f"invalid YAML recipe: {exc}")])
        diagnostics: list[Diagnostic] = []
        if recipe.get("kind") != "pipe_junction_incompressible_smoke":
            diagnostics.append(Diagnostic("error", "unsupported SimScale recipe kind"))
        if "max_compute_cpu_hours" in recipe:
            try:
                value = float(recipe["max_compute_cpu_hours"])
                if value <= 0:
                    diagnostics.append(Diagnostic("error", "max_compute_cpu_hours must be positive"))
            except (TypeError, ValueError):
                diagnostics.append(Diagnostic("error", "max_compute_cpu_hours must be numeric"))
        return LintResult(not any(d.level == "error" for d in diagnostics), diagnostics)

    def detect_installed(self) -> list[SolverInstall]:
        try:
            client = SimScaleClient.from_env()
            spaces = client.spaces()
            projects = client.projects(limit=1)
        except SimScaleApiError:
            return []
        personal = spaces.get("personalSpaces") or []
        teams = spaces.get("teamSpaces") or []
        return [
            SolverInstall(
                name=self.name,
                version="api-v0",
                path=client.config.api_url,
                source="env:SIMSCALE_API_KEY",
                extra={
                    "personal_spaces": len(personal),
                    "team_spaces": len(teams),
                    "visible_projects": _meta_total(projects),
                    "api_url": client.config.api_url,
                },
            )
        ]

    def connect(self) -> ConnectionInfo:
        try:
            client = SimScaleClient.from_env()
            spaces = client.spaces()
            projects = client.projects(limit=1)
        except SimScaleApiError as exc:
            status = "not_installed" if exc.status is None else "error"
            return ConnectionInfo(self.name, "api-v0", status, str(exc))
        personal = len(spaces.get("personalSpaces") or [])
        teams = len(spaces.get("teamSpaces") or [])
        visible_projects = _meta_total(projects)
        return ConnectionInfo(
            self.name,
            "api-v0",
            "ok",
            f"SimScale API reachable; personal_spaces={personal}, team_spaces={teams}, visible_projects={visible_projects}",
            solver_version="api-v0",
        )

    def parse_output(self, stdout: str) -> dict:
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
        return {}

    def run_file(self, script: Path) -> RunResult:
        start = time.monotonic()
        stdout = ""
        stderr = ""
        errors: list[str] = []
        artifacts: list[dict] = []
        exit_code = 0
        try:
            recipe = _load_recipe(script)
            result = self._run_recipe(recipe, recipe_path=script)
            stdout = json.dumps(result, sort_keys=True)
            for path in result.get("artifacts", []):
                artifacts.append({"path": path, "kind": "artifact"})
        except Exception as exc:  # noqa: BLE001 - one-shot path must return structured failure
            exit_code = 1
            errors.append(str(exc))
            stderr = traceback.format_exc()
        return RunResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_s=round(time.monotonic() - start, 3),
            script=str(script),
            solver=self.name,
            timestamp=_utc_now(),
            errors=errors,
            artifacts=artifacts,
        )

    def launch(self, **kwargs) -> dict:
        try:
            self._client = SimScaleClient.from_env()
            self._spaces = self._client.spaces()
            self._projects = self._client.projects(limit=10)
        except SimScaleApiError as exc:
            return {"ok": False, "error_code": "simscale.auth", "message": str(exc)}
        self._session_id = f"simscale-{uuid.uuid4().hex[:8]}"
        self._connected_at = time.time()
        self._run_count = 0
        self._last_result = {"ok": True, "command": "connect"}
        return {
            "ok": True,
            "session_id": self._session_id,
            "launch_options": {
                "api_url": self._client.config.api_url,
                "mode": kwargs.get("mode"),
                "ui_mode": kwargs.get("ui_mode"),
            },
        }

    def run(self, code: str, label: str = "") -> dict:
        if self._client is None:
            return {"ok": False, "error_code": "simscale.session.disconnected", "message": "No active SimScale session"}
        start = time.monotonic()
        try:
            command = json.loads(code)
            if not isinstance(command, dict):
                raise ValueError("command must be a JSON object")
            result = self._dispatch_command(command)
            result.setdefault("ok", True)
        except Exception as exc:  # noqa: BLE001
            result = {
                "ok": False,
                "error_code": "simscale.command.failed",
                "message": str(exc),
            }
        result["label"] = label
        result["duration_s"] = round(time.monotonic() - start, 3)
        self._last_result = result
        self._run_count += 1
        return result

    def query(self, name: str) -> dict:
        if name == "simscale.spaces":
            return {"ok": True, "spaces": self._spaces or {}}
        if name == "simscale.projects":
            return {"ok": True, "projects": self._projects or {}}
        if name == "simscale.project":
            if not self._client or not self._last_project_id:
                return {"ok": False, "error": "no last SimScale project"}
            return {"ok": True, "project": self._client.get_project(self._last_project_id)}
        if name == "simscale.run":
            if not self._client or not (self._last_project_id and self._last_simulation_id and self._last_run_id):
                return {"ok": False, "error": "no last SimScale run"}
            return {
                "ok": True,
                "run": self._client.get_simulation_run(self._last_project_id, self._last_simulation_id, self._last_run_id),
            }
        if name == "simscale.results":
            if not self._client or not (self._last_project_id and self._last_simulation_id and self._last_run_id):
                return {"ok": False, "error": "no last SimScale results"}
            return {
                "ok": True,
                "results": self._client.simulation_run_results(
                    self._last_project_id, self._last_simulation_id, self._last_run_id
                ),
            }
        raise ValueError(f"unknown query: {name}")

    def disconnect(self) -> dict:
        self._client = None
        self._session_id = None
        return {"ok": True, "disconnected": True}

    def _dispatch_command(self, command: dict) -> dict:
        assert self._client is not None
        name = command.get("command")
        if name == "list_spaces":
            self._spaces = self._client.spaces()
            return {"command": name, "spaces": self._spaces}
        if name == "list_projects":
            self._projects = self._client.projects(limit=int(command.get("limit", 100)))
            return {"command": name, "projects": self._projects}
        if name == "get_project":
            return {"command": name, "project": self._client.get_project(str(command["project_id"]))}
        if name == "get_run":
            return {
                "command": name,
                "run": self._client.get_simulation_run(
                    str(command["project_id"]), str(command["simulation_id"]), str(command["run_id"])
                ),
            }
        if name == "list_results":
            return {
                "command": name,
                "results": self._client.simulation_run_results(
                    str(command["project_id"]),
                    str(command["simulation_id"]),
                    str(command["run_id"]),
                    category=command.get("category"),
                ),
            }
        if name == "poll_run":
            return self._poll_run_command(command)
        raise ValueError(f"unsupported SimScale command: {name!r}")

    def _poll_run_command(self, command: dict) -> dict:
        assert self._client is not None
        run = self._wait_for_simulation_run(
            self._client,
            str(command["project_id"]),
            str(command["simulation_id"]),
            str(command["run_id"]),
            poll_interval_s=float(command.get("poll_interval_s", 30)),
            timeout_s=float(command.get("timeout_s", 3600)),
            event=lambda **_: None,
        )
        return {"command": "poll_run", "run": run}

    def _run_recipe(self, recipe: dict, *, recipe_path: Path) -> dict:
        if recipe.get("kind") != "pipe_junction_incompressible_smoke":
            raise ValueError("only kind=pipe_junction_incompressible_smoke is supported in v0")
        client = SimScaleClient.from_env()
        base_artifacts = Path(recipe.get("artifacts_dir") or Path.cwd() / ".sim" / "runs")
        ctx = _RunContext(client=client, recipe=recipe, artifacts_dir=base_artifacts, events=[])
        ctx.event("start", recipe=str(recipe_path), max_compute_cpu_hours=ctx.max_compute_cpu_hours)

        project_name = recipe.get("name") or f"simscale smoke {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
        project = client.create_project(
            {
                "name": project_name,
                "description": "sim-plugin-simscale guarded pipe-junction incompressible smoke run",
                "measurementSystem": "SI",
            }
        )
        project_id = str(project["projectId"])
        self._last_project_id = project_id
        ctx.event("project.created", project_id=project_id)

        storage = client.create_storage()
        storage_id = storage["storageId"]
        fixture = files("sim_plugin_simscale").joinpath("fixtures/pipe_junction_model_tutorial.x_t")
        client.put_url(storage["url"], bytes(fixture.read_bytes()))
        ctx.event("geometry.uploaded", storage_id=storage_id)

        geometry_import = client.import_geometry(project_id, _geometry_import_payload(storage_id))
        geometry_import_id = geometry_import["geometryImportId"]
        geometry_import = self._wait_for_geometry_import(client, project_id, geometry_import_id, ctx)
        geometry_id = geometry_import["geometryId"]
        ctx.event("geometry.imported", geometry_id=geometry_id)

        entities = self._pipe_entities(client, project_id, geometry_id)
        point = client.create_geometry_primitive(project_id, _probe_point_payload())
        point_uuid = point["geometryPrimitiveId"]
        ctx.event("geometry.mapped", entities=entities, point_uuid=point_uuid)

        simulation = client.create_simulation(
            project_id,
            _simulation_payload(str(project_name), geometry_id, entities, point_uuid),
        )
        simulation_id = str(simulation["simulationId"])
        self._last_simulation_id = simulation_id
        ctx.event("simulation.created", simulation_id=simulation_id)

        group_id, material_id, material_data = _find_default_water(client)
        client.update_simulation_materials(
            project_id,
            simulation_id,
            {
                "operations": [
                    {
                        "path": "/materials/fluids",
                        "materialData": material_data,
                        "reference": {"materialGroupId": group_id, "materialId": material_id},
                    }
                ]
            },
        )
        simulation_spec = client.get_simulation(project_id, simulation_id)
        fluids = simulation_spec.setdefault("model", {}).setdefault("materials", {}).setdefault("fluids", [])
        if not fluids:
            raise RuntimeError("SimScale did not create a fluid material slot after material update")
        fluids[0]["topologicalReference"] = {"entities": [entities["material"]], "sets": []}
        client.update_simulation(project_id, simulation_id, simulation_spec)
        ctx.event("simulation.material_assigned", material="Water")

        mesh_operation = client.create_mesh_operation(project_id, _mesh_operation_payload(geometry_id))
        mesh_operation_id = str(mesh_operation["meshOperationId"])
        client.update_mesh_operation(project_id, mesh_operation_id, mesh_operation)
        self._check_entries(client.check_mesh_operation(project_id, mesh_operation_id, simulation_id=simulation_id), "mesh")
        mesh_estimate = client.estimate_mesh_operation(project_id, mesh_operation_id)
        self._enforce_estimate(mesh_estimate, ctx.max_compute_cpu_hours, "mesh")
        ctx.event("mesh.estimated", estimate=mesh_estimate)
        client.start_mesh_operation(project_id, mesh_operation_id, simulation_id=simulation_id)
        ctx.event("mesh.started", mesh_operation_id=mesh_operation_id)
        mesh_operation = self._wait_for_mesh_operation(client, project_id, mesh_operation_id, ctx)
        mesh_id = mesh_operation["meshId"]
        ctx.event("mesh.finished", mesh_id=mesh_id, compute_resource=mesh_operation.get("computeResource"))

        simulation_spec = client.get_simulation(project_id, simulation_id)
        simulation_spec["meshId"] = mesh_id
        client.update_simulation(project_id, simulation_id, simulation_spec)
        self._check_entries(client.check_simulation(project_id, simulation_id), "simulation")
        simulation_estimate = client.estimate_simulation(project_id, simulation_id)
        self._enforce_estimate(simulation_estimate, ctx.max_compute_cpu_hours, "simulation")
        ctx.event("simulation.estimated", estimate=simulation_estimate)

        run = client.create_simulation_run(project_id, simulation_id, {"name": "Run 1"})
        run_id = str(run["runId"])
        self._last_run_id = run_id
        client.update_simulation_run(project_id, simulation_id, run_id, run)
        client.start_simulation_run(project_id, simulation_id, run_id)
        ctx.event("run.started", run_id=run_id)
        run = self._wait_for_simulation_run(
            client,
            project_id,
            simulation_id,
            run_id,
            poll_interval_s=ctx.poll_interval_s,
            timeout_s=max(3600, ctx.poll_interval_s * 4),
            event=lambda **fields: ctx.event("run.poll", **fields),
        )
        ctx.event("run.finished", status=run.get("status"), compute_resource=run.get("computeResource"))

        run_artifacts_dir = ctx.artifacts_dir / run_id
        results = client.simulation_run_results(project_id, simulation_id, run_id, category="PROBE_POINT_PLOT")
        probe = _embedded(results)[0]
        csv_bytes = client.download_url(probe["download"]["url"])
        probe_path = run_artifacts_dir / "probe_points.csv"
        probe_path.parent.mkdir(parents=True, exist_ok=True)
        probe_path.write_bytes(csv_bytes)
        events_path = run_artifacts_dir / "events.json"
        metadata_path = run_artifacts_dir / "metadata.json"
        metadata = {
            "ok": run.get("status") == "FINISHED",
            "project_id": project_id,
            "simulation_id": simulation_id,
            "run_id": run_id,
            "mesh_operation_id": mesh_operation_id,
            "geometry_id": geometry_id,
            "mesh_id": mesh_id,
            "status": run.get("status"),
            "progress": run.get("progress"),
            "compute_resource": run.get("computeResource"),
            "started_at": run.get("startedAt"),
            "finished_at": run.get("finishedAt"),
            "workbench_url": f"https://www.simscale.com/workbench/?pid={project_id}",
            "probe_result_count": _meta_total(results),
            "artifacts": [str(probe_path), str(events_path), str(metadata_path)],
        }
        _write_json(events_path, ctx.events)
        _write_json(metadata_path, metadata)
        self._last_result = metadata
        return metadata

    def _pipe_entities(self, client: SimScaleClient, project_id: str, geometry_id: str) -> dict[str, str]:
        return {
            "material": self._single_entity(client, project_id, geometry_id, "Fluid Region"),
            "inlet1": self._single_entity(client, project_id, geometry_id, "Face ZMAX"),
            "inlet2": self._single_entity(client, project_id, geometry_id, "Face Junction"),
            "outlet": self._single_entity(client, project_id, geometry_id, "Face YMAX"),
        }

    def _single_entity(self, client: SimScaleClient, project_id: str, geometry_id: str, value: str) -> str:
        mappings = _embedded(
            client.geometry_mappings(project_id, geometry_id, attributes=["SDL/TYSA_NAME"], values=[value])
        )
        if len(mappings) != 1:
            raise RuntimeError(f"expected exactly one geometry mapping for {value!r}, found {len(mappings)}")
        return str(mappings[0]["name"])

    def _wait_for_geometry_import(
        self, client: SimScaleClient, project_id: str, geometry_import_id: str, ctx: _RunContext
    ) -> dict:
        deadline = time.monotonic() + 900
        while True:
            item = client.get_geometry_import(project_id, geometry_import_id)
            status = item.get("status")
            ctx.event("geometry.poll", status=status)
            if status in TERMINAL_STATUSES:
                if status != "FINISHED":
                    raise RuntimeError(f"geometry import ended with status {status}")
                return item
            if time.monotonic() > deadline:
                raise TimeoutError("geometry import timed out")
            time.sleep(ctx.poll_interval_s)

    def _wait_for_mesh_operation(
        self, client: SimScaleClient, project_id: str, mesh_operation_id: str, ctx: _RunContext
    ) -> dict:
        deadline = time.monotonic() + 3600
        while True:
            item = client.get_mesh_operation(project_id, mesh_operation_id)
            status = item.get("status")
            ctx.event("mesh.poll", status=status, progress=item.get("progress"))
            if status in TERMINAL_STATUSES:
                if status != "FINISHED":
                    raise RuntimeError(f"mesh operation ended with status {status}")
                return item
            if time.monotonic() > deadline:
                raise TimeoutError("mesh operation timed out")
            time.sleep(ctx.poll_interval_s)

    def _wait_for_simulation_run(
        self,
        client: SimScaleClient,
        project_id: str,
        simulation_id: str,
        run_id: str,
        *,
        poll_interval_s: float,
        timeout_s: float,
        event,
    ) -> dict:
        deadline = time.monotonic() + timeout_s
        while True:
            item = client.get_simulation_run(project_id, simulation_id, run_id)
            status = item.get("status")
            event(status=status, progress=item.get("progress"))
            if status in TERMINAL_STATUSES:
                if status != "FINISHED":
                    raise RuntimeError(f"simulation run ended with status {status}")
                return item
            if time.monotonic() > deadline:
                raise TimeoutError("simulation run timed out")
            time.sleep(poll_interval_s)

    def _check_entries(self, payload: dict, label: str) -> None:
        entries = payload.get("entries") or []
        errors = [e for e in entries if e.get("severity") == "ERROR"]
        if errors:
            raise RuntimeError(f"{label} check failed: {errors}")

    def _enforce_estimate(self, estimate: dict, limit: float, label: str) -> None:
        value = _resource_value(estimate)
        if value is not None and value > limit:
            raise RuntimeError(f"{label} estimate {value} CPU-hours exceeds limit {limit}")
