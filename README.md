# sim-plugin-simscale

Use Codex, Claude Code, or another AI agent to work with
[SimScale](https://www.simscale.com/) cloud simulations through
[sim-cli](https://github.com/svd-ai-lab/sim-cli).

`sim-plugin-simscale` makes SimScale a cloud backend in sim's cross-solver
runtime. It is not a general replacement for the SimScale API or Workbench.
The first alpha release focuses on safe API access checks, session inspection,
and one guarded smoke recipe.

SimScale access, API keys, and compute credits are not bundled. See
[LICENSE-NOTICE.md](LICENSE-NOTICE.md).

## Current Maturity

This is an initial alpha. It has unit coverage and one real-cloud smoke path
for accounts with SimScale API access and enough credits.

The only built-in recipe in `0.1.0` is:

```yaml
kind: pipe_junction_incompressible_smoke
name: My SimScale smoke run
max_compute_cpu_hours: 0.5
```

The recipe uploads a tiny pipe-junction Parasolid fixture, imports geometry,
creates a water incompressible-flow setup, meshes, solves, and downloads a
probe-point CSV.

## Install

GitHub install is the primary channel for `0.1.0`:

```bash
uv pip install "git+https://github.com/svd-ai-lab/sim-plugin-simscale.git@v0.1.0"
```

For source testing against the current main branch:

```bash
uv pip install "git+https://github.com/svd-ai-lab/sim-plugin-simscale.git@main"
```

## Authentication

Create a SimScale API key in your SimScale account and export it locally:

```bash
export SIMSCALE_API_KEY="..."
```

On PowerShell:

```powershell
$env:SIMSCALE_API_KEY = "..."
```

Optional:

```bash
export SIMSCALE_API_URL="https://api.simscale.com"
```

Never commit API keys. Rotate keys that were pasted into chat or logs.

## Common Workflow

Check API access:

```bash
sim check simscale
```

Start an API-backed session:

```bash
sim connect --solver simscale --ui-mode no_gui
sim inspect session.summary
sim inspect simscale.spaces
```

Run bounded JSON commands:

```bash
sim exec '{"command": "list_projects"}'
sim exec '{"command": "list_results", "project_id": "...", "simulation_id": "...", "run_id": "..."}'
```

Run the smoke recipe:

```bash
sim run --solver simscale recipe.yaml
```

Artifacts are written under `.sim/runs/<run-id>/`:

- `metadata.json`
- `events.json`
- `probe_points.csv`

## Credit Safety

The smoke recipe calls SimScale estimate endpoints before starting meshing or
solving. By default it aborts if either estimate exceeds `0.5` CPU-hours.

You can lower the gate in the recipe:

```yaml
max_compute_cpu_hours: 0.2
```

Successful API calls can still create projects/uploads. Compute credit is only
expected after mesh or simulation run start calls.

## Develop

```bash
git clone https://github.com/svd-ai-lab/sim-plugin-simscale
cd sim-plugin-simscale
uv sync --extra test
uv run pytest -q
uv build
```

Live smoke testing is opt-in:

```bash
SIMSCALE_API_KEY=... SIMSCALE_RUN_INTEGRATION=1 uv run pytest tests/test_live_smoke.py -q
```

## License

Apache-2.0. See [LICENSE](LICENSE) and [LICENSE-NOTICE.md](LICENSE-NOTICE.md).
