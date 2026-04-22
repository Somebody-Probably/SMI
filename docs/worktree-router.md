# Worktree Isolation And Router Merging

SMI workers can run inside per-slot git worktrees. This gives each agent an isolated checkout and preserves any file edits as a branch commit.

## Worker Isolation

```bash
research-smi worker \
  --run-id smoke-001 \
  --lane fast_local \
  --slots 1 \
  --worktrees \
  --repo-root .
```

Worktrees are created under `.smi-worktrees/` and branches use this pattern:

```text
smi/<run-id>/<slot-id>
```

If a worker changes files, those changes are committed on that branch after the task exits.

## Router Review

List pending branches:

```bash
research-smi router --repo-root . list --run-id smoke-001
```

Preview a merge:

```bash
research-smi router --repo-root . plan smi/smoke-001/fast_local-00
```

Merge after human or router review:

```bash
research-smi router --repo-root . merge smi/smoke-001/fast_local-00
```

The merge command requires a clean worktree and cherry-picks reviewed commits into the active branch. Agents should not merge their own branches without explicit operator policy.

