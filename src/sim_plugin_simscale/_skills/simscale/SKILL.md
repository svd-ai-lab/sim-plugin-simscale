---
name: simscale
description: "Use SimScale through sim-cli: API-key checks, cloud project/session inspection, and guarded smoke recipes with compute-credit limits."
---

# SimScale via sim

Use this skill when driving SimScale through `sim-plugin-simscale`.

## Safety First

- Never print or commit `SIMSCALE_API_KEY`.
- Treat pasted keys as compromised and rotate after validation.
- Check estimates before starting mesh or simulation compute.
- Record the SimScale project ID, simulation ID, run ID, compute resource, and
  Workbench URL in the final evidence.

## Basic Commands

```bash
sim check simscale
sim connect --solver simscale --ui-mode no_gui
sim inspect session.summary
sim inspect simscale.spaces
sim exec '{"command": "list_projects"}'
sim disconnect
```

## Smoke Recipe

`sim-plugin-simscale` v0 supports one guarded recipe:

```yaml
kind: pipe_junction_incompressible_smoke
name: Agent smoke run
max_compute_cpu_hours: 0.5
```

Run it with:

```bash
sim run --solver simscale recipe.yaml
```

Expected artifacts:

- `.sim/runs/<run-id>/metadata.json`
- `.sim/runs/<run-id>/events.json`
- `.sim/runs/<run-id>/probe_points.csv`

## Validation Guidance

For routine code changes, use mocked tests and `uv build`; do not start cloud
compute. For release validation, run one live smoke from a fresh install, then
inspect the downloaded probe CSV and metadata.

Stop and report if:

- `sim check simscale` cannot see spaces or projects.
- estimate exceeds the requested CPU-hour limit.
- SimScale reports authorization, billing, or credit errors.
- geometry mappings for the bundled fixture are not unique.
