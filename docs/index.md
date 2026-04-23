# Documentation Index

Start here when onboarding a new researcher.

## New User Path

1. Read `MANUAL.md`.
2. Follow `docs/mac-setup.md` to install Python tools and configure SSH.
3. On Windows, follow `docs/windows-setup.md` for the local harness instead of
   the macOS SSH setup.
4. Configure `config/globus.json` and run `research-globus doctor`.
5. Run `research-smi-doctor`.
6. Run the local SMI smoke test from `README.md`.
7. Open one MFA-backed cluster session with `research-cluster connect`.
8. Start the account SMI watcher before direct cluster work or project swarms.

## Operator References

- `docs/cluster-workflow.md`: SSH ControlMaster, rsync, SLURM submit, status, and harvest flow.
- `docs/smi-manager-protocol.md`: SMI run state, lanes, tasks, orders, and worker protocol.
- `docs/smi-general-harness-plan.md`: cross-platform harness roadmap and private agent-market boundary.
- `docs/smi-cluster-throttling.md`: account SMI and project SMI coordination model.
- `docs/smi-agent-spawning.md`: Codex and Claude CLI agent worker spawning.
- `docs/smi-e2e-cluster-tests.md`: direct account and project-swarm cluster smoke tests.
- `docs/windows-setup.md`: Windows 10 local SMI harness and acceptance gate.
- `docs/worktree-router.md`: isolated agent worktrees and reviewed merge flow.

## Chemistry References

- `docs/computational-chemistry.md`: chemistry batch workflow overview.
- `docs/chemistry-smoke-tests.md`: ORCA, Quantum ESPRESSO, CP2K, and SIESTA smoke jobs.
- `docs/qe-orca-templates.md`: QE and ORCA manifest-worker templates.
- `docs/chemistry-parsers.md`: parser and retry-order helpers.

## Transfer And Site Notes

- `docs/globus-transfers.md`: optional Globus transfer workflow.
- `docs/alliance-clusters.md`: Alliance cluster notes and current renewal aliases.
- `docs/third-party-notices.md`: attribution and external inspiration.
