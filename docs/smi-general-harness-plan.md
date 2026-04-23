# SMI General Harness Working Plan

This plan turns SMI into a stable cross-platform harness before expanding the
private agent-market research layer. The public repository should remain small,
auditable, and useful to research groups. Deeper bidding, staking, and model
reputation experiments belong in private testbeds until the mechanics are
well-understood.

## North Star

SMI should coordinate heterogeneous agents and conventional cluster jobs through
one accountable lifecycle:

```text
task spec -> claim -> lease -> execute -> observe -> verify -> reconcile -> learn
```

Every task should answer:

- What is the intended work?
- Which files or resources may it touch?
- Who claimed it?
- What did it produce?
- What independent check accepted or rejected it?
- What should the controller believe after the result?

## Public Harness Boundary

Public SMI should focus on:

- local task orchestration with SQLite and filesystem artifacts;
- MacBook and Windows local smoke tests;
- account-level cluster throttling;
- project-level agent workers;
- chemistry calculation management and retry recommendations;
- worktree isolation and router-reviewed merge flow.

Public SMI should not yet expose:

- API-agent bidding;
- stake/slashing language;
- model/effort reputation markets;
- automated trust scores that could be mistaken for validated scientific
  authority;
- opaque private benchmark data.

The public vocabulary can still prepare for this future by using neutral terms:
`policy`, `reputation`, `verification`, `attempt history`, and `admission`.

## Platform Roles

| Platform | Role | Required checks |
| --- | --- | --- |
| Windows 10 | Fast local compatibility harness | install, pytest, dry-run SMI, orders, worktrees, Codex command smoke |
| MacBook | Primary researcher-facing path | install, SSH/Globus doctor, local SMI, account watcher, cluster dry-runs |
| Cluster | Remote execution target | small SLURM smoke, queue snapshot, harvest, parser/retry loop |
| Private lab | Frontier controller experiments | model/effort calibration, bidding, reputation, adversarial verification |

## Milestone 0: Baseline Sync

Goal: make Windows and MacBook agents reason from the same branch.

Tasks:

- keep a named integration branch for public-harness work;
- avoid editing stale local clones without `git fetch`;
- document which branch Windows tested and which branch MacBook tested;
- record command transcripts only as short summaries in PRs or release notes.

Acceptance:

- `git status -sb` is clean before each platform run;
- both agents report commit SHA and OS in their test notes.

## Milestone 1: Windows Local Harness

Goal: prove SMI can run locally on Windows without cluster access.

Tasks:

- maintain `docs/windows-setup.md`;
- verify path handling in prompt materialization, run directories, orders, and
  worktree roots;
- run `python -m pytest`;
- run dry-run SMI init, worker, status, and hot-folder order checks;
- test Codex preset in dry-run mode before real agent spawning.

Acceptance:

- all pytest tests pass on Windows;
- a dry-run result artifact is written under the configured run root;
- order processing archives input JSON under `orders/processed`;
- worktree mode does not fail on Windows paths.

## Milestone 2: MacBook Cross-Platform Gate

Goal: repeat the same harness checks on the researcher-facing system.

Tasks:

- run the local smoke path from `README.md`;
- run `research-smi-doctor --strict`;
- run Globus doctor and plan rendering with example or real config as
  appropriate;
- run SSH and cluster commands in `--dry-run` mode first;
- confirm account watcher state files render clean JSON and Markdown.

Acceptance:

- Windows and MacBook both pass local SMI checks;
- MacBook confirms docs are accurate for macOS shell syntax;
- any platform-specific commands are isolated in platform docs.

## Milestone 3: Durable Runtime Semantics

Goal: make the public harness robust enough for real parallel agent work.

Tasks:

- implement background subprocess supervision so one manager can run multiple
  active workers concurrently;
- add periodic heartbeats while workers are running;
- enforce lease expiry and retry/hold policy;
- unblock dependency-blocked tasks when prerequisites complete;
- add schema version reporting and migration checks;
- add a compact event inspection command for debugging.

Acceptance:

- a long-running local dummy command keeps its lease alive;
- a killed worker is requeued or held with a clear failure class;
- dependency chains progress without manual status edits;
- event logs explain each state transition.

## Milestone 4: Verification And Reconciliation Gate

Goal: separate production from judgment.

Tasks:

- add a project-neutral verification command that checks declared write sets,
  expected artifacts, optional validation commands, and suspicious unfinished
  markers;
- make chemistry verification read parser outputs and scheduler logs rather
  than agent claims;
- mark tasks as accepted, rejected, or held for review;
- require router-reviewed merge for worktree commits.

Acceptance:

- workers cannot self-certify success;
- write-set violations are rejected or held;
- chemistry retry orders cite observed parser results;
- router merge remains a deliberate operator action.

## Milestone 5: End-To-End Chemistry Harness

Goal: provide one boring, reliable researcher demo.

Tasks:

- generate QE and ORCA specs from local examples;
- dry-run SMI project tasks;
- submit one tiny cluster smoke job through the account gate when credentials
  are available;
- harvest outputs into a run directory;
- parse outputs and create retry orders only when evidence supports it.

Acceptance:

- local dry-run works on Windows and MacBook;
- cluster smoke works on MacBook plus real account;
- parser/retry behavior is reproducible from saved artifacts;
- the manual can be followed by a new researcher without private context.

## Private Research Track: Agent Market

The endgame is a market of heterogeneous API and CLI agents that bid on tasks
and stake reputation on `(model, effort, policy)` choices. This needs private
testing because the failure modes are subtle:

- agents may bid low and underperform;
- agents may overclaim success;
- models may differ by task family, not global quality;
- effort settings may have nonlinear cost/quality curves;
- verification can become a target if rewards are public;
- reputation must distinguish bad luck, hard tasks, and bad behavior.

Private milestones:

- collect model/effort calibration data on controlled task families;
- define task families and verification rubrics before scoring agents;
- record attempt histories without exposing sensitive prompts or raw outputs;
- simulate bids offline before allowing live dispatch decisions;
- introduce reputation as an advisory policy input, not a hard scheduler rule;
- test adversarial cases where agents try to game apparent success.

Public transition rule:

Only expose market-facing features after the public harness has durable runtime
semantics, independent verification, and enough private evidence to explain
what the scores mean and what they do not mean.

## Immediate Next Work

1. Land this plan and the Windows setup doc.
2. Run the Windows acceptance gate on this machine.
3. Ask the MacBook agent to run the same branch through macOS local checks.
4. Open the first implementation slice for durable runtime semantics:
   background worker supervision, heartbeats, and lease expiry.
5. Keep the agent-market experiments private and evidence-driven.

