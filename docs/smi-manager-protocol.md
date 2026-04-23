# SMI Manager Protocol

SMI is a small manager protocol for coordinating research tasks across local workers and remote resources.

## Core Objects

- `run`: an orchestration epoch.
- `lane`: a capacity domain such as `fast_local`, `heavy_local`, `verify_local`, or `remote_cluster`.
- `slot`: a worker endpoint inside a lane.
- `task`: a unit of intended work with a prompt, priority, dependencies, write set, and metadata.
- `attempt`: one concrete execution of a task.
- `lease`: the active ownership token for a task assigned to a slot.
- `event`: append-only state transition record.

The SQLite database is the queue state. Filesystem directories hold prompts, results, processed orders, and the JSONL event log.

## Run Directory

```text
runs/<run-id>/
  run.db
  events.jsonl
  prompts/
  results/
  orders/
    processed/
```

## Inspecting State

Use `status` for queue, lane, slot, and lease summaries:

```bash
research-smi status --run-id <run-id>
research-smi status --run-id <run-id> --json
```

Use `events` for recent append-only transition records:

```bash
research-smi events --run-id <run-id> --limit 20
research-smi events --run-id <run-id> --type lease.expired --json
```

The event log is also mirrored as JSONL at `runs/<run-id>/events.jsonl`.

## Verification Records

Task execution and task acceptance are separate. A worker can complete an
attempt, but a verifier records whether that completed attempt should be
accepted, rejected, or held for review:

```bash
research-smi verify --run-id <run-id>
research-smi verify --run-id <run-id> --task-id <task-id> --json
```

The first neutral verifier checks completed attempts and records evidence in the
SQLite `verifications` table plus a `verification.<decision>` event. By default,
it accepts completed attempts that have no failed process result. Use
`--require-artifacts` to hold attempts whose declared `write_set` entries or
`metadata.expected_output` paths are missing:

```bash
research-smi verify --run-id <run-id> --require-artifacts
```

This initial verifier records decisions without changing dependency behavior.
Downstream reconciliation can later decide how accepted, rejected, and held
records should affect merges, retries, and public reports.

## Task Spec

```json
{
  "tasks": [
    {
      "id": "short-name",
      "lane": "fast_local",
      "priority": 100,
      "dependencies": [],
      "write_set": ["path/to/file.md"],
      "prompt": "Full self-contained task prompt."
    }
  ]
}
```

Inline `prompt` values are materialized into `runs/<run-id>/prompts/<task-id>.md`.

## Hot-Folder Orders

Drop JSON files into `runs/<run-id>/orders/`, then run:

```bash
research-smi orders --run-id <run-id>
```

Supported order types:

- `seed`: add tasks.
- `cancel`: mark tasks cancelled.
- `reprioritize`: update priorities.
- `pause`: pause dispatch.
- `resume`: resume dispatch.
- `drain`: stop new dispatch and let current work finish.
- `lane_config`: resize lanes or change admission state.

Example seed order:

```json
{
  "order_type": "seed",
  "payload": {
    "tasks": [
      {
        "id": "followup-task",
        "lane": "fast_local",
        "priority": 150,
        "write_set": ["notes/followup.md"],
        "prompt": "Create notes/followup.md with one paragraph."
      }
    ]
  }
}
```

## Dependencies

Tasks can name dependencies with task IDs in the same run. A dependent task is
stored as `blocked` until every dependency reaches `completed`; then SMI moves
it to `ready`.

This is useful for cluster-friendly workflows:

```text
stage inputs -> submit job -> monitor job -> harvest outputs -> parse/report
```

Use lane capacities to throttle the expensive phases, such as staging and
harvesting, while letting cheap monitoring tasks run separately.

## Leases And Heartbeats

When a worker claims a task, SMI creates an active lease tying the task,
attempt, and slot together. The slot records `last_heartbeat_at`, and the lease
records `expires_at`.

Parallel workers renew active leases while subprocesses are still running. Each
renewal writes a `lease.heartbeat` event with the refreshed expiry time. Before a
worker claims more work in its lane, it reaps stale active leases whose
`expires_at` has passed:

- the stale lease becomes `expired`;
- the attempt becomes `failed` with `failure_class=lease_expired`;
- the task returns to `retry_ready`;
- the slot returns to `idle`;
- SMI writes a `lease.expired` event.

This protects the queue when a worker process or controller exits without
releasing its lease. It does not kill an external process; it reconciles SMI
state so another attempt can be scheduled.

## Agent Workers

Workers can run a CLI agent for each claimed task. The backward-compatible
default is `claude --print`, but `--agent codex` builds a Codex command that
reads the task prompt from stdin:

```bash
research-smi worker \
  --run-id SMI_project_1 \
  --lane fast_local \
  --agent codex \
  --codex-model gpt-5.4
```

Use `--agent-command` when a lab needs a fully custom command.

## Account Gates

The account gate is intended to be the always-on cluster access layer. Separate
project runs can share it for cluster-facing lanes, and projects that do not
need a swarm can still use the account layer directly for remote work:

```bash
research-smi account init --account-id drac-fouquet
research-smi account watch --account-id drac-fouquet
research-smi run --run-id SMI_project_1 --account-gate-id drac-fouquet
research-smi run --run-id SMI_project_2 --account-gate-id drac-fouquet
```

The default gated lanes are `remote_transfer`, `remote_submit`,
`remote_monitor`, and `remote_cluster`. A worker in one of these lanes must
acquire a permit from `runs/SMI_account_<account-id>/account_gate.sqlite`
before claiming a task.

The gate is SQLite-backed, so workers do not require the watcher process to be
up at the instant they acquire a permit. The watcher is the persistent operator
loop for visibility, stale-permit reaping, and future account-level keepalive
policy.

The watcher also writes visible local state:

```text
runs/SMI_account_<account-id>/account_state.json
runs/SMI_account_<account-id>/account_state.md
```

These files include lane capacities, active permits, session touch results, and
optional queue snapshots. Project agents should prefer this file for routine
queue awareness.
