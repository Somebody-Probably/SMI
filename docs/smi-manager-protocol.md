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

