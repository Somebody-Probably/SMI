import subprocess
from pathlib import Path

from research_cluster_smi.router import Router
from research_cluster_smi.worktree import WorktreeManager


def git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout.strip()


def test_worktree_preserve_and_router_plan(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Test User")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "base")

    manager = WorktreeManager(repo)
    worktree = manager.ensure_worktree("run-001", "fast_local-00")
    (worktree / "agent-output.md").write_text("worker result\n", encoding="utf-8")
    preserved = manager.preserve_changes("run-001", "fast_local-00", "task-001")

    assert preserved.commit is not None
    router = Router(repo)
    pending = router.pending_branches("run-001")
    assert len(pending) == 1
    assert pending[0].branch == "smi/run-001/fast_local-00"
    plan = router.merge_branch(pending[0].branch, dry_run=True)
    assert plan["dry_run"] is True
    assert "agent-output.md" in plan["changed_files"]

