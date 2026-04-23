# Windows 10 Local Harness

This guide keeps Windows 10 as a first-class SMI test target. The goal is a
local, cluster-free harness that can validate core SMI behavior before the
MacBook agent repeats the same checks on macOS and before any researcher touches
real cluster accounts.

## Scope

Windows should prove these paths:

- package installation in a local virtual environment;
- SMI run initialization, prompt materialization, task claiming, dry-run worker
  completion, and status rendering;
- hot-folder seed orders;
- Codex or custom CLI command construction without launching an unsafe task;
- worktree isolation when Git is installed and configured;
- chemistry spec generation and parser/retry-order behavior on local files.

Windows does not need to prove SSH ControlMaster, Globus endpoint access, or
SLURM behavior. Those remain MacBook and cluster-side checks.

## Prerequisites

Use PowerShell from the repository root.

```powershell
py -3 --version
git --version
```

Optional agent checks need `codex` on `PATH`:

```powershell
codex --version
```

If PowerShell blocks virtual environment activation for the current process:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

## Install

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
research-smi-doctor --local
```

For repeatable Windows smoke runs, keep run state outside the repo's long-lived
`runs/` directory:

```powershell
$env:SMI_RUN_ROOT = "$PWD\runs-windows"
```

## Core SMI Smoke

```powershell
research-smi init --run-id win-smoke-001 --spec examples\hello-task-spec.json
research-smi worker --run-id win-smoke-001 --lane fast_local --slots 1 --dry-run --once
research-smi status --run-id win-smoke-001
```

Expected result:

- one completed task;
- `runs-windows\win-smoke-001\events.jsonl` exists;
- `runs-windows\win-smoke-001\results\hello\result.json` is a dry-run result.

## Hot-Folder Order Smoke

```powershell
@'
{
  "order_type": "seed",
  "payload": {
    "tasks": [
      {
        "id": "win-order-hello",
        "lane": "fast_local",
        "priority": 150,
        "write_set": ["notes/win-order-hello.md"],
        "prompt": "Create notes/win-order-hello.md with one short paragraph."
      }
    ]
  }
}
'@ | Set-Content -Encoding UTF8 runs-windows\win-smoke-001\orders\seed-win-order.json

research-smi orders --run-id win-smoke-001
research-smi worker --run-id win-smoke-001 --lane fast_local --slots 1 --dry-run --once
research-smi status --run-id win-smoke-001
```

Expected result:

- the order moves to `orders\processed\`;
- task count increases by one completed task;
- the materialized prompt appears under `prompts\win-order-hello.md`.

## Worktree Smoke

Run this only inside a Git checkout with user identity configured:

```powershell
git config user.name
git config user.email

research-smi init --run-id win-worktree-001 --spec examples\hello-task-spec.json
research-smi worker --run-id win-worktree-001 --lane fast_local --slots 1 --dry-run --worktrees --repo-root . --once
research-smi router --repo-root . list --run-id win-worktree-001
```

Dry-run workers should not create commits, but the command should create or
reuse a per-slot worktree without path errors.

## Codex Command Smoke

This checks command generation and task routing. Use dry-run first:

```powershell
research-smi init --run-id win-codex-001 --spec examples\hello-task-spec.json
research-smi worker --run-id win-codex-001 --lane fast_local --slots 1 --agent codex --codex-model gpt-5.4 --dry-run --once
```

Only run a real Codex task after reviewing the prompt and write set:

```powershell
research-smi worker --run-id win-codex-001 --lane fast_local --slots 1 --agent codex --codex-model gpt-5.4 --worktrees --repo-root . --once
```

## Chemistry Local Smoke

```powershell
research-chem make-spec --program qe --input-dir examples\qe\input --output runs-windows\qe-generated-spec.json
research-smi init --run-id win-qe-001 --spec runs-windows\qe-generated-spec.json
research-smi worker --run-id win-qe-001 --lane remote_cluster --slots 1 --dry-run --once
research-smi status --run-id win-qe-001
```

Parser tests should use checked-in sample outputs when available. If a local
output file is not present, skip parser execution rather than inventing one.

## Windows Acceptance Gate

Before asking the MacBook agent to cross-check a branch, Windows should report:

```powershell
python -m pytest
research-smi-doctor --local
research-smi init --run-id win-smoke-gate --spec examples\hello-task-spec.json
research-smi run --run-id win-smoke-gate --lanes fast_local --slots 1 --dry-run --once
```

Record failures with the exact command, exit code, and the shortest useful
diagnostic excerpt.
