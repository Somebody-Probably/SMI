# SMI Cluster Throttling

SMI is most useful when one cluster account has several active projects and
agents could otherwise create transfer, submit, or polling spikes. The account
layer is the always-on baseline; project swarms are optional.

Use one shared `SMI_account_*` gate for cluster-facing resources. Add
`SMI_project_*` runs only for projects that need parallel CLI-agent work,
project-local dependencies, and worktree isolation.

Here, "account" means the scheduler/accounting boundary we want to protect,
usually the user's login plus the SLURM account/allocation used for submissions
on a cluster. It is not a lab-wide or group-wide scheduler.

## Current Runtime Model

SMI already provides:

- SQLite-backed tasks, attempts, leases, slots, lanes, and events.
- Lane-level capacity through `max_slots`.
- Hot-folder orders for seed, cancel, reprioritize, pause, resume, drain, and
  lane resizing.
- Write-set conflict avoidance so two active tasks do not edit or harvest the
  same declared files.
- Dependency release: a blocked task becomes ready when all task dependencies
  complete.
- Account gates: independent project runs can share account-scoped permits for
  remote lanes.

The important operational rule is that all cluster-touching work for the same
login/account should pass through the same `SMI_account_*` gate. Separate
`SMI_project_*` run IDs without an account gate still have separate SQLite
databases and therefore separate throttles.

## Account-Level Lane Profile

Start an account-scoped run when you want the account SMI to own remote tasks
directly:

```bash
research-smi init \
  --run-id drac-account \
  --lanes-json config/lanes.cluster-account.example.json
```

Initialize the account gate used by all project swarms:

```bash
research-smi account init \
  --account-id drac-fouquet \
  --resources-json config/lanes.cluster-account.example.json
```

Keep the account layer visible in a long-running terminal:

```bash
research-smi account watch \
  --account-id drac-fouquet \
  --resources-json config/lanes.cluster-account.example.json \
  --touch-sessions \
  --queue-snapshot
```

The watcher writes `account_state.json` and `account_state.md` under
`runs/SMI_account_<account-id>/`. Swarm agents should read this snapshot before
deciding whether they need a direct cluster query.

Use the specialized remote lanes:

- `remote_transfer`: stage input files, pseudopotentials, basis files, and
  harvest outputs. Keep this at `max_slots=1` by default.
- `remote_submit`: open/touch sessions, submit jobs, or cancel jobs. Keep this
  at `max_slots=1` to avoid scheduler bursts.
- `remote_monitor`: lightweight `squeue`, `sacct`, and output-health checks.
  This can usually run at `max_slots=2`.
- `remote_cluster`: compatibility lane for older all-in-one calculation
  management tasks.

## Recommended Task Shape

Prefer small chained tasks over one long all-in-one remote task:

```text
stage inputs -> submit job -> monitor job -> harvest outputs -> parse/report
```

This shape lets SMI throttle only the expensive phases. A submitted SLURM job
does not need to occupy a transfer slot while it waits or runs on the scheduler.

Example task fields:

```json
{
  "id": "project-a-stage-water",
  "lane": "remote_transfer",
  "priority": 120,
  "write_set": ["remote/project-a/water/input"],
  "metadata": {
    "project": "project-a",
    "cluster": "fir",
    "phase": "stage",
    "estimated_transfer_mb": 25
  },
  "prompt": "Stage the Project A water inputs to Fir."
}
```

The next task can depend on it:

```json
{
  "id": "project-a-submit-water",
  "lane": "remote_submit",
  "dependencies": ["project-a-stage-water"],
  "write_set": ["remote/project-a/water/job-id.txt"],
  "metadata": {
    "project": "project-a",
    "cluster": "fir",
    "phase": "submit"
  },
  "prompt": "Submit the staged Project A water job to Fir."
}
```

For separate project swarms, initialize each project with:

```bash
research-smi init \
  --run-id SMI_project_1 \
  --lanes-json config/lanes.project-swarm.example.json
```

Then run project workers against the account gate:

```bash
research-smi run \
  --run-id SMI_project_1 \
  --lanes fast_local,heavy_local,verify_local \
  --agent codex \
  --worktrees \
  --repo-root .
```

Do not use account permits as a whole-project mutex. For swarm agents, launch
local agents in `fast_local` or `heavy_local` and have the agent acquire
account permits only for the remote phase it is performing. The account gate
should parallelize work up to each resource limit and wait only when that
resource is saturated.

## Backpressure Operations

Pause all new work:

```bash
research-smi pause --run-id drac-account
```

Drain after current tasks finish:

```bash
research-smi drain --run-id drac-account
```

Resize a lane by dropping an order into `runs/drac-account/orders/`:

```json
{
  "order_type": "lane_config",
  "payload": {
    "lanes": {
      "remote_transfer": {"max_slots": 1},
      "remote_monitor": {"max_slots": 2}
    }
  }
}
```

Then process it:

```bash
research-smi orders --run-id drac-account
```

## Next Optimizations

The current implementation is strong enough to coordinate several projects when
they share the account gate, and it now has a lightweight account resource
ledger for independent project runs. The next scaling step is richer policy
around that ledger, such as:

- `transfer_slots` for rsync/scp/Globus activity.
- `submit_slots` or submit tokens for `sbatch` bursts.
- per-cluster polling cadence for `squeue` and `sacct`.
- optional project fairness weights so one project cannot starve others.
- cluster-session keepalive tied to the always-on account watcher.
