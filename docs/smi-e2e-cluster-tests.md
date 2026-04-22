# SMI End-To-End Cluster Tests

This test proves both supported cluster paths:

- direct account SMI: `SMI_account_*` submits, monitors, and harvests jobs;
- project swarm: `SMI_project_*` spawns worker agents that use the account SMI
  gate before touching the cluster.

## Account State Snapshot

Run the account watcher with queue snapshots:

```bash
research-smi account watch \
  --account-id drac-fouquet \
  --resources-json config/lanes.cluster-account.example.json \
  --clusters rorqual,trillium \
  --touch-sessions \
  --queue-snapshot
```

The watcher refreshes:

```text
runs/SMI_account_drac-fouquet/account_state.json
runs/SMI_account_drac-fouquet/account_state.md
```

Swarm agents should read these files before deciding to poll a cluster
directly. The JSON file contains account lane usage, active permits, session
touch results, and recent `squeue`/`sacct` snapshots.

## Direct Account Path

```bash
RUN_ID="smi-e2e-$(date -u +%Y%m%d-%H%M%S)"
mkdir -p "runs/${RUN_ID}"

research-smi account cluster-smoke \
  --account-id drac-fouquet \
  --resources-json config/lanes.cluster-account.example.json \
  --cluster-config config/clusters.json \
  --clusters rorqual,trillium \
  --label "${RUN_ID}-account-direct" \
  --local-output-dir "runs/${RUN_ID}/account-direct" \
  --profile smoke \
  --poll-interval 10 \
  --timeout 900
```

Cluster workflows are launched concurrently by default. The account permits
serialize only saturated phases: for example, `remote_submit=1` allows one
submit at a time while already-submitted jobs continue running remotely, and
`remote_monitor=2` allows two queue polls at once.

Expected outputs:

```text
runs/<RUN_ID>/account-direct/<cluster>/summary.json
runs/<RUN_ID>/account-direct/<cluster>/results/smi-e2e-<jobid>.txt
runs/<RUN_ID>/account-direct/<cluster>/logs/
```

## Project Swarm Path

Create a task spec whose prompts are JSON payloads for the deterministic
cluster smoke agent:

```json
{
  "tasks": [
    {
      "id": "agent-rorqual",
      "lane": "fast_local",
      "write_set": ["cluster-smoke/rorqual"],
      "prompt": "{\"cluster\":\"rorqual\",\"label\":\"<RUN_ID>-project-agent-rorqual\",\"local_output_dir\":\"runs/<RUN_ID>/project-agent\",\"profile\":\"smoke\"}"
    },
    {
      "id": "agent-trillium",
      "lane": "fast_local",
      "write_set": ["cluster-smoke/trillium"],
      "prompt": "{\"cluster\":\"trillium\",\"label\":\"<RUN_ID>-project-agent-trillium\",\"local_output_dir\":\"runs/<RUN_ID>/project-agent\",\"profile\":\"smoke\"}"
    }
  ]
}
```

Then run:

```bash
research-smi init \
  --run-id "SMI_project_${RUN_ID}" \
  --lanes-json config/lanes.project-swarm.example.json \
  --spec "runs/${RUN_ID}/project-agent-spec.json"

research-smi worker \
  --run-id "SMI_project_${RUN_ID}" \
  --lane fast_local \
  --slots 2 \
  --parallel \
  --exit-when-idle \
  --tick-interval 5 \
  --agent-command "research-smi agent cluster-smoke --account-id drac-fouquet --cluster-config config/clusters.json --profile smoke --poll-interval 10 --timeout 900"
```

Expected outputs:

```text
runs/<RUN_ID>/project-agent/<cluster>/summary.json
runs/<RUN_ID>/project-agent/<cluster>/results/smi-e2e-<jobid>.txt
runs/SMI_project_<RUN_ID>/results/<task-id>/result.json
runs/SMI_project_<RUN_ID>/results/<task-id>/stdout.txt
```

The project SMI launches both agents locally. Each agent then talks to the
account SMI for `remote_submit`, `remote_monitor`, and `remote_transfer`
permits, so only saturated cluster-facing phases wait.
