"""Small REST client for the SimScale API.

The public SimScale Python SDK is currently distributed from GitHub. This
plugin intentionally keeps the runtime dependency-free apart from sim-cli-core
and PyYAML by using the documented REST API directly.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


class SimScaleApiError(RuntimeError):
    """HTTP/API failure with safe, non-secret context."""

    def __init__(self, status: int | None, message: str, *, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


@dataclass(frozen=True)
class SimScaleConfig:
    api_key: str
    api_url: str = "https://api.simscale.com"
    timeout_s: float = 60.0

    @classmethod
    def from_env(cls) -> "SimScaleConfig":
        api_key = os.environ.get("SIMSCALE_API_KEY", "").strip()
        if not api_key:
            raise SimScaleApiError(
                None,
                "SIMSCALE_API_KEY is not set; create a SimScale API key and export it before using this plugin.",
            )
        api_url = os.environ.get("SIMSCALE_API_URL", "https://api.simscale.com").rstrip("/")
        return cls(api_key=api_key, api_url=api_url)


def _quote_path(value: str) -> str:
    return urllib.parse.quote(str(value), safe="")


class SimScaleClient:
    """Tiny JSON/file client for the SimScale v0 API."""

    def __init__(self, config: SimScaleConfig | None = None):
        self.config = config or SimScaleConfig.from_env()
        self.base_v0 = self.config.api_url.rstrip("/") + "/v0"

    @classmethod
    def from_env(cls) -> "SimScaleClient":
        return cls(SimScaleConfig.from_env())

    def _headers(self, *, json_body: bool = False) -> dict[str, str]:
        headers = {
            "X-API-KEY": self.config.api_key,
            "Accept": "application/json",
            "User-Agent": "sim-plugin-simscale/0.1.0",
        }
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _request(
        self,
        method: str,
        url: str,
        *,
        body: Any = None,
        headers: dict[str, str] | None = None,
        raw: bytes | None = None,
        expect_json: bool = True,
        timeout_s: float | None = None,
    ) -> Any:
        data: bytes | None
        request_headers = headers or self._headers(json_body=body is not None)
        if raw is not None:
            data = raw
        elif body is not None:
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        else:
            data = None
        req = urllib.request.Request(url, data=data, headers=request_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout_s or self.config.timeout_s) as resp:
                payload = resp.read()
                if not expect_json:
                    return payload
                if not payload:
                    return None
                return json.loads(payload.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            parsed: Any = None
            message = f"SimScale API returned HTTP {exc.code}"
            if payload:
                text = payload.decode("utf-8", errors="replace")
                try:
                    parsed = json.loads(text)
                    message = parsed.get("message") or message
                except json.JSONDecodeError:
                    parsed = {"text": text[:1000]}
            raise SimScaleApiError(exc.code, message, body=parsed) from exc
        except urllib.error.URLError as exc:
            raise SimScaleApiError(None, f"cannot reach SimScale API: {exc.reason}") from exc

    def _url(self, path: str, query: dict[str, Any] | None = None) -> str:
        url = self.base_v0 + path
        if query:
            clean = {k: v for k, v in query.items() if v is not None}
            url += "?" + urllib.parse.urlencode(clean, doseq=True)
        return url

    def get(self, path: str, query: dict[str, Any] | None = None) -> Any:
        return self._request("GET", self._url(path, query))

    def post(self, path: str, body: Any | None = None, query: dict[str, Any] | None = None) -> Any:
        return self._request("POST", self._url(path, query), body=body or {})

    def put(self, path: str, body: Any | None = None) -> Any:
        return self._request("PUT", self._url(path), body=body or {})

    def put_url(self, url: str, data: bytes, *, content_type: str = "application/octet-stream") -> Any:
        headers = {
            "Content-Type": content_type,
            "User-Agent": "sim-plugin-simscale/0.1.0",
        }
        return self._request("PUT", url, raw=data, headers=headers, expect_json=False)

    def download_url(self, url: str) -> bytes:
        return self._request("GET", url, headers=self._headers(), expect_json=False, timeout_s=120)

    def spaces(self) -> dict:
        return self.get("/spaces")

    def projects(self, *, page: int = 1, limit: int = 100) -> dict:
        return self.get("/projects", {"page": page, "limit": limit})

    def space_root_projects(self, space_id: str, *, page: int = 1, limit: int = 100) -> dict:
        return self.get(f"/spaces/{_quote_path(space_id)}/content/projects", {"page": page, "limit": limit})

    def material_groups(self) -> dict:
        return self.get("/materialgroups")

    def materials(self, material_group_id: str) -> dict:
        return self.get(f"/materialgroups/{_quote_path(material_group_id)}/materials")

    def material(self, material_group_id: str, material_id: str) -> dict:
        return self.get(
            f"/materialgroups/{_quote_path(material_group_id)}/materials/{_quote_path(material_id)}"
        )

    def create_storage(self) -> dict:
        return self.post("/storage")

    def create_project(self, body: dict) -> dict:
        return self.post("/projects", body)

    def get_project(self, project_id: str) -> dict:
        return self.get(f"/projects/{_quote_path(project_id)}")

    def import_geometry(self, project_id: str, body: dict) -> dict:
        return self.post(f"/projects/{_quote_path(project_id)}/geometryimports", body)

    def get_geometry_import(self, project_id: str, geometry_import_id: str) -> dict:
        return self.get(
            f"/projects/{_quote_path(project_id)}/geometryimports/{_quote_path(geometry_import_id)}"
        )

    def geometry_mappings(self, project_id: str, geometry_id: str, *, attributes: list[str], values: list[str]) -> dict:
        query: dict[str, Any] = {
            "limit": 100,
            "page": 1,
            "attributes": attributes,
            "values": values,
        }
        return self.get(f"/projects/{_quote_path(project_id)}/geometries/{_quote_path(geometry_id)}/mappings", query)

    def create_geometry_primitive(self, project_id: str, body: dict) -> dict:
        return self.post(f"/projects/{_quote_path(project_id)}/geometryprimitives", body)

    def create_simulation(self, project_id: str, body: dict) -> dict:
        return self.post(f"/projects/{_quote_path(project_id)}/simulations", body)

    def get_simulation(self, project_id: str, simulation_id: str) -> dict:
        return self.get(f"/projects/{_quote_path(project_id)}/simulations/{_quote_path(simulation_id)}")

    def update_simulation(self, project_id: str, simulation_id: str, body: dict) -> dict:
        return self.put(f"/projects/{_quote_path(project_id)}/simulations/{_quote_path(simulation_id)}", body)

    def update_simulation_materials(self, project_id: str, simulation_id: str, body: dict) -> dict:
        return self.post(
            f"/projects/{_quote_path(project_id)}/simulations/{_quote_path(simulation_id)}/materials",
            body,
        )

    def check_simulation(self, project_id: str, simulation_id: str) -> dict:
        return self.post(f"/projects/{_quote_path(project_id)}/simulations/{_quote_path(simulation_id)}/check")

    def estimate_simulation(self, project_id: str, simulation_id: str) -> dict:
        return self.post(f"/projects/{_quote_path(project_id)}/simulations/{_quote_path(simulation_id)}/estimate")

    def create_mesh_operation(self, project_id: str, body: dict) -> dict:
        return self.post(f"/projects/{_quote_path(project_id)}/meshoperations", body)

    def update_mesh_operation(self, project_id: str, mesh_operation_id: str, body: dict) -> dict:
        return self.put(f"/projects/{_quote_path(project_id)}/meshoperations/{_quote_path(mesh_operation_id)}", body)

    def check_mesh_operation(self, project_id: str, mesh_operation_id: str, *, simulation_id: str) -> dict:
        return self.post(
            f"/projects/{_quote_path(project_id)}/meshoperations/{_quote_path(mesh_operation_id)}/check",
            query={"simulationId": simulation_id},
        )

    def estimate_mesh_operation(self, project_id: str, mesh_operation_id: str) -> dict:
        return self.post(f"/projects/{_quote_path(project_id)}/meshoperations/{_quote_path(mesh_operation_id)}/estimate")

    def start_mesh_operation(self, project_id: str, mesh_operation_id: str, *, simulation_id: str) -> dict:
        return self.post(
            f"/projects/{_quote_path(project_id)}/meshoperations/{_quote_path(mesh_operation_id)}/start",
            query={"simulationId": simulation_id},
        )

    def get_mesh_operation(self, project_id: str, mesh_operation_id: str) -> dict:
        return self.get(f"/projects/{_quote_path(project_id)}/meshoperations/{_quote_path(mesh_operation_id)}")

    def mesh_operation_event_log(self, project_id: str, mesh_operation_id: str) -> dict:
        return self.get(f"/projects/{_quote_path(project_id)}/meshoperations/{_quote_path(mesh_operation_id)}/eventlog")

    def create_simulation_run(self, project_id: str, simulation_id: str, body: dict) -> dict:
        return self.post(
            f"/projects/{_quote_path(project_id)}/simulations/{_quote_path(simulation_id)}/runs",
            body,
        )

    def update_simulation_run(self, project_id: str, simulation_id: str, run_id: str, body: dict) -> dict:
        return self.put(
            f"/projects/{_quote_path(project_id)}/simulations/{_quote_path(simulation_id)}/runs/{_quote_path(run_id)}",
            body,
        )

    def start_simulation_run(self, project_id: str, simulation_id: str, run_id: str) -> dict:
        return self.post(
            f"/projects/{_quote_path(project_id)}/simulations/{_quote_path(simulation_id)}/runs/{_quote_path(run_id)}/start"
        )

    def get_simulation_run(self, project_id: str, simulation_id: str, run_id: str) -> dict:
        return self.get(
            f"/projects/{_quote_path(project_id)}/simulations/{_quote_path(simulation_id)}/runs/{_quote_path(run_id)}"
        )

    def simulation_run_event_log(self, project_id: str, simulation_id: str, run_id: str) -> dict:
        return self.get(
            f"/projects/{_quote_path(project_id)}/simulations/{_quote_path(simulation_id)}/runs/{_quote_path(run_id)}/eventlog"
        )

    def simulation_run_results(
        self,
        project_id: str,
        simulation_id: str,
        run_id: str,
        *,
        category: str | None = None,
        page: int = 1,
        limit: int = 100,
    ) -> dict:
        return self.get(
            f"/projects/{_quote_path(project_id)}/simulations/{_quote_path(simulation_id)}/runs/{_quote_path(run_id)}/results",
            {"page": page, "limit": limit, "category": category},
        )
