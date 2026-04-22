# SMI Agent Spawning

The Claude-era spawning hook is still present, but it was generic enough that
we can now point it at Codex. SMI workers read each task prompt and pass it to a
CLI command over stdin.

## Current State

Still present:

- `research-smi worker` and `research-smi run` can launch a CLI agent per
  claimed task.
- `--agent-command` can run any command that accepts the prompt on stdin.
- `SMI_AGENT_COMMAND` still works for local overrides.
- `--worktrees` creates per-slot git worktrees and preserves worker edits as
  branch commits for router review.

Important limits:

- The legacy default command is still `claude --print` for backward
  compatibility.
- One worker manager loops over its slots synchronously. True parallelism comes
  from running multiple worker processes, or from future async process
  supervision.
- Stdout/stderr are captured after the subprocess exits; streaming logs are not
  implemented yet.

## Codex Preset

Use the Codex preset for GPT/Codex CLI spawning:

```bash
research-smi worker \
  --run-id SMI_project_1 \
  --lane fast_local \
  --slots 1 \
  --agent codex \
  --codex-model gpt-5.4 \
  --worktrees \
  --repo-root .
```

Internally this builds a command shaped like:

```bash
codex exec --model gpt-5.4 --sandbox workspace-write -
```

The trailing `-` tells `codex exec` to read the task prompt from stdin. You can
override the generated command completely:

```bash
research-smi worker \
  --run-id SMI_project_1 \
  --lane fast_local \
  --agent-command "codex exec --model gpt-5.4 --sandbox danger-full-access -"
```

Useful environment overrides:

```bash
export SMI_AGENT_PRESET=codex
export SMI_CODEX_MODEL=gpt-5.4
export SMI_CODEX_SANDBOX=workspace-write
export SMI_CODEX_EXTRA_ARGS="--ephemeral"
```

## Always-On Account Plus Optional Project Swarm

The account layer is the baseline and should exist even when no project needs a
parallel agent swarm. Project SMI runs are optional: use them when a project
benefits from multiple local CLI agents, isolated worktrees, and a project-local
task graph.

The intended shape is:

```text
SMI_account_fouquet -> always-on shared cluster access gate
SMI_project_1        -> optional local swarm and project task graph
SMI_project_2        -> optional local swarm and project task graph
```

Initialize the shared account gate once, then keep it visible with `watch`:

```bash
research-smi account init \
  --account-id drac-fouquet \
  --resources-json config/lanes.cluster-account.example.json

research-smi account watch \
  --account-id drac-fouquet \
  --resources-json config/lanes.cluster-account.example.json
```

Projects that do not need a swarm can submit remote work directly to the
account-level workflow. Projects that do need a swarm initialize their own
project run:

```bash
research-smi init \
  --run-id SMI_project_1 \
  --lanes-json config/lanes.project-swarm.example.json
```

Then run project workers locally. Remote-aware agents should acquire account
permits for their own cluster-facing phases:

```bash
research-smi run \
  --run-id SMI_project_1 \
  --lanes fast_local,heavy_local,verify_local \
  --slots 1 \
  --agent codex \
  --codex-model gpt-5.4 \
  --worktrees \
  --repo-root .
```

When a worker lane is one of `remote_transfer`, `remote_submit`,
`remote_monitor`, or `remote_cluster`, the worker must acquire a shared permit
from `SMI_account_drac-fouquet` before it claims and runs a project task. If
another project is already using the account permit, the task remains ready and
the worker tries again on the next tick.

The gate itself is SQLite-backed, so permit acquisition still works if the
watch process is restarted. The watch process is the operational home for
account visibility, stale-permit reaping, and future cluster-session keepalive
logic.

For spawned agents that run a multi-phase cluster workflow, prefer launching
the agents from a local project lane such as `fast_local` and let the agent
acquire account permits for each remote phase. Holding `remote_cluster` around
an entire agent is conservative and should be reserved for legacy all-in-one
tasks.

Run the watcher with queue snapshots when swarm agents should inspect cluster
state without opening their own SSH connections:

```bash
research-smi account watch \
  --account-id drac-fouquet \
  --resources-json config/lanes.cluster-account.example.json \
  --clusters rorqual,trillium \
  --touch-sessions \
  --queue-snapshot
```

This refreshes:

```text
runs/SMI_account_drac-fouquet/account_state.json
runs/SMI_account_drac-fouquet/account_state.md
```

Agents can also print the current snapshot with:

```bash
research-smi account state --account-id drac-fouquet
```

Check shared account pressure:

```bash
research-smi account status --account-id drac-fouquet
```

Manually clear a stuck permit only after confirming the worker is gone:

```bash
research-smi account release \
  --account-id drac-fouquet \
  --permit-id permit-xxxxxxxxxxxx
```

## Next Spawning Optimizations

The current implementation gives us the first useful layer: Codex-compatible
task dispatch plus account-level semaphores across independent project runs.
The next improvements should be:

- background subprocess supervision so one manager can truly run several
  agents at once;
- streaming per-task logs;
- project fairness weights in the account gate;
- a policy that encourages agents to split remote work into stage, submit,
  monitor, harvest, and parse tasks instead of holding a remote permit for an
  entire calculation lifecycle.
